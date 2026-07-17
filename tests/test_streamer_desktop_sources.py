"""
tests/test_streamer_desktop_sources.py

Tests the direct-capture additions to robonet/gst_io/streamer_unencrypted.py:
  - _VideoPipeline: VIDEO_SOURCE_XIMAGESRC captures the desktop directly
    via ximagesrc instead of v4l2src reading a device path.
  - _AudioPipeline: AUDIO_SOURCE_DESKTOP_MIX mixes the desktop's system
    audio (PulseAudio monitor) and mic directly via two pulsesrc
    branches into an audiomixer, instead of a single alsasrc reading a
    loopback device.

Both replace what used to be a v4l2loopback/ALSA-loopback round trip
that produced blank output in practice -- these feed straight into the
same encode/send chain used for any real camera/mic.

Gst.ElementFactory.make is mocked throughout: what's under test is which
elements get created/linked/configured, not whether specific GStreamer
plugins happen to be installed on whatever machine runs the tests.
"""

import unittest
from unittest.mock import patch, MagicMock

import numpy as np

from robonet.gst_io.streamer_unencrypted import (
    _VideoPipeline, _AudioPipeline, VIDEO_SOURCE_XIMAGESRC, AUDIO_SOURCE_DESKTOP_MIX,
)


def _make_factory_mock():
    """Gst.ElementFactory.make side_effect: returns a fresh MagicMock per
    call, tagged with which factory name created it so assertions can
    find "the pulsesrc for the mic branch" etc without relying on call
    order."""
    created = {}

    def make(factory_name, elem_name):
        mock = MagicMock(name=f'{factory_name}:{elem_name}')
        created.setdefault(factory_name, []).append((elem_name, mock))
        return mock

    return make, created


class TestVideoPipelineXimagesrc(unittest.TestCase):

    def _make(self, src_device):
        return _VideoPipeline(src_device=src_device, enc_name='x264enc', video_codec='h264',
                              receiver_ip='10.0.0.5', width=640, height=480, fps=30, bitrate=2_000_000)

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_desktop_source_creates_ximagesrc_not_v4l2src(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make(VIDEO_SOURCE_XIMAGESRC).build()

        self.assertTrue(ok)
        self.assertIn('ximagesrc', created)
        self.assertNotIn('v4l2src', created)

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_desktop_source_sets_use_damage_not_device(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        self._make(VIDEO_SOURCE_XIMAGESRC).build()

        _, ximagesrc_elem = created['ximagesrc'][0]
        ximagesrc_elem.set_property.assert_any_call('use-damage', False)
        for call in ximagesrc_elem.set_property.call_args_list:
            self.assertNotEqual(call.args[0], 'device')

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_real_device_path_still_uses_v4l2src(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make('/dev/video0').build()

        self.assertTrue(ok)
        self.assertIn('v4l2src', created)
        self.assertNotIn('ximagesrc', created)
        _, v4l2_elem = created['v4l2src'][0]
        v4l2_elem.set_property.assert_any_call('device', '/dev/video0')


class TestAudioPipelineDesktopMix(unittest.TestCase):

    def _make(self, mic_device):
        return _AudioPipeline(mic_device=mic_device, enc_name='opusenc', audio_codec='opus',
                              server_ip='10.0.0.5', sample_rate=48000)

    @patch('robonet.gst_io.streamer_unencrypted._pactl_get_default')
    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_mixes_monitor_and_distinct_mic(self, mock_make, _pipeline_new, mock_pactl):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn
        mock_pactl.side_effect = lambda field: {'sink': 'builtin-speaker', 'source': 'builtin-mic'}[field]

        ok = self._make(AUDIO_SOURCE_DESKTOP_MIX).build()

        self.assertTrue(ok)
        self.assertIn('audiomixer', created)
        self.assertEqual(len(created.get('pulsesrc', [])), 2)  # monitor branch + mic branch
        device_values = [call.args[1] for _, elem in created['pulsesrc']
                        for call in elem.set_property.call_args_list if call.args[0] == 'device']
        self.assertIn('builtin-speaker.monitor', device_values)
        self.assertIn('builtin-mic', device_values)
        self.assertNotIn('alsasrc', created)

    @patch('robonet.gst_io.streamer_unencrypted._pactl_get_default')
    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_skips_mic_when_it_equals_monitor(self, mock_make, _pipeline_new, mock_pactl):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn
        # source happens to report the same name as the monitor -- avoid double-counting it
        mock_pactl.side_effect = lambda field: {'sink': 'x', 'source': 'x.monitor'}[field]

        ok = self._make(AUDIO_SOURCE_DESKTOP_MIX).build()

        self.assertTrue(ok)
        self.assertEqual(len(created.get('pulsesrc', [])), 1)

    @patch('robonet.gst_io.streamer_unencrypted._pactl_get_default', return_value=None)
    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_no_sink_or_source_found_fails_gracefully(self, mock_make, _pipeline_new, _pactl):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make(AUDIO_SOURCE_DESKTOP_MIX).build()

        self.assertFalse(ok)  # no crash, just reports failure so the caller can try the next encoder/etc

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_real_alsa_device_still_uses_alsasrc(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make('hw:1,0,0').build()

        self.assertTrue(ok)
        self.assertIn('alsasrc', created)
        self.assertNotIn('audiomixer', created)
        _, alsa_elem = created['alsasrc'][0]
        alsa_elem.set_property.assert_any_call('device', 'hw:1,0,0')

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_array_source_uses_appsrc(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        pipe = _AudioPipeline(mic_device='default', enc_name='opusenc', audio_codec='opus',
                              server_ip='10.0.0.5', sample_rate=48000,
                              array_source=np.zeros(100, dtype=np.float32))
        ok = pipe.build()

        self.assertTrue(ok)
        self.assertIn('appsrc', created)
        self.assertNotIn('alsasrc', created)
        self.assertNotIn('audiomixer', created)
        self.assertNotIn('audiotestsrc', created)
        _, src_elem = created['appsrc'][0]
        src_elem.set_property.assert_any_call('is-live', True)


class TestPactlGetDefault(unittest.TestCase):

    @patch('robonet.gst_io.streamer_unencrypted.subprocess.run')
    def test_returns_stripped_stdout(self, mock_run):
        mock_run.return_value = MagicMock(stdout='some-sink-name\n')
        from robonet.gst_io.streamer_unencrypted import _pactl_get_default
        self.assertEqual(_pactl_get_default('sink'), 'some-sink-name')

    @patch('robonet.gst_io.streamer_unencrypted.subprocess.run', side_effect=Exception('pactl not found'))
    def test_returns_none_on_failure(self, _run):
        from robonet.gst_io.streamer_unencrypted import _pactl_get_default
        self.assertIsNone(_pactl_get_default('sink'))


if __name__ == '__main__':
    unittest.main()


class TestToS16le(unittest.TestCase):

    def test_float_in_range_converts_to_int16(self):
        from robonet.gst_io.streamer_unencrypted import _to_s16le
        out = _to_s16le(np.array([0.0, 0.5, -1.0, 1.0], dtype=np.float32))
        self.assertEqual(out.dtype, np.int16)
        np.testing.assert_array_equal(out, [0, 16383, -32767, 32767])

    def test_out_of_range_float_is_clipped_not_wrapped(self):
        from robonet.gst_io.streamer_unencrypted import _to_s16le
        out = _to_s16le(np.array([2.0, -2.0], dtype=np.float32))
        np.testing.assert_array_equal(out, [32767, -32767])

    def test_int16_passes_through_unchanged(self):
        from robonet.gst_io.streamer_unencrypted import _to_s16le
        arr = np.array([1, -1, 12345], dtype=np.int16)
        out = _to_s16le(arr)
        self.assertIs(out, arr)


class TestAudioPipelineArrayFeed(unittest.TestCase):
    """_feed_array is what actually runs on the background thread in
    production; called directly here (it's a plain synchronous method)
    with a sub-one-chunk array so the test completes in one real
    ~20ms sleep rather than mocking time.sleep."""

    def _make_pipe(self, on_complete=None):
        pipe = _AudioPipeline(mic_device='default', enc_name='opusenc', audio_codec='opus',
                              server_ip='10.0.0.5', sample_rate=48000,
                              array_source=np.zeros(100, dtype=np.float32),
                              on_array_complete=on_complete)
        pipe._appsrc = MagicMock()
        pipe._effective_rate = 48000
        return pipe

    def test_pushes_a_buffer_and_signals_eos(self):
        pipe = self._make_pipe()
        pipe._feed_array()
        emitted = [c.args[0] for c in pipe._appsrc.emit.call_args_list]
        self.assertIn('push-buffer', emitted)
        self.assertEqual(emitted[-1], 'end-of-stream')

    def test_calls_on_complete_when_finished_normally(self):
        on_complete = MagicMock()
        pipe = self._make_pipe(on_complete=on_complete)
        pipe._feed_array()
        on_complete.assert_called_once()

    def test_does_not_call_on_complete_if_stopped_early(self):
        on_complete = MagicMock()
        pipe = self._make_pipe(on_complete=on_complete)
        pipe._feed_stop.set()  # simulate stop() having been called first
        pipe._feed_array()
        on_complete.assert_not_called()

    def test_appsrc_error_does_not_raise_and_still_reports_complete(self):
        # A poisoned appsrc must not crash the feeder thread silently --
        # same guarantee as the receive-side callback hardening.
        on_complete = MagicMock()
        pipe = self._make_pipe(on_complete=on_complete)
        pipe._appsrc.emit.side_effect = RuntimeError('boom')
        pipe._feed_array()  # must not raise
        on_complete.assert_called_once()


class TestGstSenderPlayArray(unittest.TestCase):

    def _make_sender(self, connected=True, known_encoder=('opusenc', 'opus')):
        from robonet.gst_io.streamer_unencrypted import GstSender
        s = GstSender.__new__(GstSender)
        s._receiver_ip = '10.0.0.5' if connected else None
        s._audio_candidates = [('opusenc', 'opus'), ('avenc_aac', 'aac')]
        s._aenc_name, s._audio_codec = known_encoder if known_encoder else (None, None)
        s._mic_device = 'default'
        s._apipe = None
        return s

    def test_returns_false_when_not_connected(self):
        s = self._make_sender(connected=False)
        ok = s.play_array(np.zeros(10, dtype=np.float32))
        self.assertFalse(ok)

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_reuses_the_already_known_encoder(self, mock_pipeline_cls):
        s = self._make_sender(known_encoder=('opusenc', 'opus'))
        mock_pipeline_cls.return_value.build.return_value = True

        s.play_array(np.zeros(10, dtype=np.float32), sample_rate=48000)

        _, kwargs = mock_pipeline_cls.call_args
        args = mock_pipeline_cls.call_args[0]
        self.assertEqual(args[1], 'opusenc')  # enc_name -- not re-probed from candidates[0]

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_falls_back_to_first_candidate_when_no_encoder_known_yet(self, mock_pipeline_cls):
        s = self._make_sender(known_encoder=None)
        mock_pipeline_cls.return_value.build.return_value = True

        s.play_array(np.zeros(10, dtype=np.float32))

        args = mock_pipeline_cls.call_args[0]
        self.assertEqual(args[1], 'opusenc')  # first of _audio_candidates

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_stops_any_existing_pipeline_first(self, mock_pipeline_cls):
        s = self._make_sender()
        old_pipe = MagicMock()
        s._apipe = old_pipe
        mock_pipeline_cls.return_value.build.return_value = True

        s.play_array(np.zeros(10, dtype=np.float32))

        old_pipe.stop.assert_called_once()

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_build_failure_returns_false(self, mock_pipeline_cls):
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = False

        ok = s.play_array(np.zeros(10, dtype=np.float32))

        self.assertFalse(ok)

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_on_success_the_new_pipeline_becomes_apipe_and_starts_playing(self, mock_pipeline_cls):
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = True

        ok = s.play_array(np.zeros(10, dtype=np.float32))

        self.assertTrue(ok)
        self.assertIs(s._apipe, mock_pipeline_cls.return_value)
        mock_pipeline_cls.return_value.play.assert_called_once()


class TestAudioStreamHandle(unittest.TestCase):
    """Streaming mode: chunks arrive over time via push(), the SAME
    appsrc/encoder/RTP session stays alive across all of them -- no
    per-chunk pipeline rebuild, avoiding the encoder-reset artifacts a
    naive "call play_array() once per chunk" approach would cause."""

    def _make_pipe(self):
        pipe = _AudioPipeline(mic_device='default', enc_name='opusenc', audio_codec='opus',
                              server_ip='10.0.0.5', sample_rate=48000, streaming=True)
        pipe._appsrc = MagicMock()
        pipe._effective_rate = 48000
        return pipe

    def test_push_chunk_enqueues_without_blocking(self):
        pipe = self._make_pipe()
        pipe.push_chunk(np.zeros(100, dtype=np.float32))
        self.assertEqual(pipe._stream_queue.qsize(), 1)

    def test_feed_stream_pushes_every_queued_chunk_then_eos_after_end(self):
        pipe = self._make_pipe()
        pipe.push_chunk(np.zeros(100, dtype=np.float32))
        pipe.push_chunk(np.zeros(100, dtype=np.float32))
        pipe.end_stream()

        pipe._feed_stream()

        emitted = [c.args[0] for c in pipe._appsrc.emit.call_args_list]
        self.assertGreaterEqual(emitted.count('push-buffer'), 2)  # at least one push per chunk
        self.assertEqual(emitted[-1], 'end-of-stream')

    def test_no_eos_while_still_streaming_with_no_data_yet(self):
        # end_stream() not called -- must not give up just because the
        # queue is momentarily empty (that's normal mid-stream).
        pipe = self._make_pipe()
        pipe._feed_stop = MagicMock()
        # Trip the stop flag after a couple of empty-queue polls so the
        # test doesn't spin forever waiting for data that never comes.
        pipe._feed_stop.is_set.side_effect = [False, False, False, True]

        pipe._feed_stream()

        emitted = [c.args[0] for c in pipe._appsrc.emit.call_args_list]
        self.assertNotIn('end-of-stream', emitted)  # never told to end

    def test_on_complete_fires_after_a_clean_end(self):
        on_complete = MagicMock()
        pipe = self._make_pipe()
        pipe._on_array_complete = on_complete
        pipe.end_stream()

        pipe._feed_stream()

        on_complete.assert_called_once()

    def test_stopped_early_does_not_call_on_complete(self):
        on_complete = MagicMock()
        pipe = self._make_pipe()
        pipe._on_array_complete = on_complete
        pipe._feed_stop.set()

        pipe._feed_stream()

        on_complete.assert_not_called()

    def test_handle_push_and_end_delegate_to_the_pipeline(self):
        from robonet.gst_io.streamer_unencrypted import AudioStreamHandle
        pipe = MagicMock()
        handle = AudioStreamHandle(pipe)
        chunk = np.zeros(10, dtype=np.float32)

        handle.push(chunk)
        handle.end()

        pipe.push_chunk.assert_called_once_with(chunk)
        pipe.end_stream.assert_called_once()


class TestGstSenderStartAudioStream(unittest.TestCase):

    def _make_sender(self, connected=True):
        from robonet.gst_io.streamer_unencrypted import GstSender
        s = GstSender.__new__(GstSender)
        s._receiver_ip = '10.0.0.5' if connected else None
        s._audio_candidates = [('opusenc', 'opus')]
        s._aenc_name, s._audio_codec = 'opusenc', 'opus'
        s._mic_device = 'default'
        s._apipe = None
        return s

    def test_returns_none_when_not_connected(self):
        s = self._make_sender(connected=False)
        self.assertIsNone(s.start_audio_stream())

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_returns_a_handle_on_success(self, mock_pipeline_cls):
        from robonet.gst_io.streamer_unencrypted import AudioStreamHandle
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = True

        handle = s.start_audio_stream()

        self.assertIsInstance(handle, AudioStreamHandle)
        mock_pipeline_cls.return_value.play.assert_called_once()

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_passes_streaming_true_and_requested_channels(self, mock_pipeline_cls):
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = True

        s.start_audio_stream(channels=2)

        _, kwargs = mock_pipeline_cls.call_args
        self.assertTrue(kwargs['streaming'])
        self.assertEqual(kwargs['channels'], 2)

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_build_failure_returns_none(self, mock_pipeline_cls):
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = False
        self.assertIsNone(s.start_audio_stream())


class TestPlayArrayChannelInference(unittest.TestCase):
    """channels is read from the array's own shape, not a separate
    parameter -- can't disagree with what was actually passed."""

    def _make_sender(self):
        from robonet.gst_io.streamer_unencrypted import GstSender
        s = GstSender.__new__(GstSender)
        s._receiver_ip = '10.0.0.5'
        s._audio_candidates = [('opusenc', 'opus')]
        s._aenc_name, s._audio_codec = 'opusenc', 'opus'
        s._mic_device = 'default'
        s._apipe = None
        return s

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_1d_array_infers_mono(self, mock_pipeline_cls):
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = True
        s.play_array(np.zeros(10, dtype=np.float32))
        self.assertEqual(mock_pipeline_cls.call_args.kwargs['channels'], 1)

    @patch('robonet.gst_io.streamer_unencrypted._AudioPipeline')
    def test_2d_array_infers_channel_count_from_shape(self, mock_pipeline_cls):
        s = self._make_sender()
        mock_pipeline_cls.return_value.build.return_value = True
        s.play_array(np.zeros((10, 2), dtype=np.float32))
        self.assertEqual(mock_pipeline_cls.call_args.kwargs['channels'], 2)
