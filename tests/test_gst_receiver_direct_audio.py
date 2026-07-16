"""
tests/test_gst_receiver_direct_audio.py

Tests robonet/gst_io/receiver_unencrypted.py's GstReceiver.set_direct_audio
and the _build_audio_pipeline helper it shares with on_stream_info --
the receiver-side redirection mechanism setup_ai_audio_input uses to
route received audio straight into a PipeWire virtual device instead of
only through the Python recv_audio_callback.
"""

import unittest

import numpy as np
from unittest.mock import patch, MagicMock

from robonet.gst_io.receiver_unencrypted import GstReceiver


class _FakeInfo:
    def __init__(self, audio_codec='opus'):
        self.audio_codec = audio_codec


class _FakeGstReceiver:
    """Minimal GstReceiver stand-in carrying just the attributes
    _build_audio_pipeline/set_direct_audio actually touch."""
    def __init__(self, info=None, direct_audio=False, audio_device='default',
                recv_audio_callback=None):
        self._info = info
        self._direct_audio = direct_audio
        self._audio_device = audio_device
        self._recv_audio_callback = recv_audio_callback
        self._play_locally = False
        self._apipe = None

    _build_audio_pipeline = GstReceiver._build_audio_pipeline
    set_direct_audio = GstReceiver.set_direct_audio


class TestBuildAudioPipeline(unittest.TestCase):

    def test_noop_when_no_info(self):
        fake = _FakeGstReceiver(info=None)
        fake._build_audio_pipeline()
        self.assertIsNone(fake._apipe)

    def test_noop_when_info_has_no_audio_codec(self):
        fake = _FakeGstReceiver(info=_FakeInfo(audio_codec=None))
        fake._build_audio_pipeline()
        self.assertIsNone(fake._apipe)

    @patch('robonet.gst_io.receiver_unencrypted._audio_decoder_candidates', return_value=['opusdec'])
    @patch('robonet.gst_io.receiver_unencrypted._AudioRecvPipeline')
    def test_builds_and_plays_on_success(self, mock_pipe_cls, _candidates):
        mock_pipe = MagicMock()
        mock_pipe.build.return_value = True
        mock_pipe.probe.return_value = True
        mock_pipe_cls.return_value = mock_pipe

        fake = _FakeGstReceiver(info=_FakeInfo())
        fake._build_audio_pipeline()

        mock_pipe.play.assert_called_once()
        self.assertIs(fake._apipe, mock_pipe)

    @patch('robonet.gst_io.receiver_unencrypted._audio_decoder_candidates', return_value=['opusdec'])
    @patch('robonet.gst_io.receiver_unencrypted._AudioRecvPipeline')
    def test_direct_audio_with_callback_set_suppresses_python_callback(self, mock_pipe_cls, _candidates):
        mock_pipe = MagicMock()
        mock_pipe.build.return_value = True
        mock_pipe.probe.return_value = True
        mock_pipe_cls.return_value = mock_pipe

        fake = _FakeGstReceiver(info=_FakeInfo(), direct_audio=True,
                                recv_audio_callback=MagicMock())
        fake._build_audio_pipeline()

        call = mock_pipe_cls.call_args
        passed_on_audio = call.kwargs.get('on_audio')
        self.assertIsNone(passed_on_audio)  # direct_audio wins -- python callback suppressed

    @patch('robonet.gst_io.receiver_unencrypted._audio_decoder_candidates', return_value=['opusdec'])
    @patch('robonet.gst_io.receiver_unencrypted._AudioRecvPipeline')
    def test_no_direct_audio_still_uses_python_callback(self, mock_pipe_cls, _candidates):
        mock_pipe = MagicMock()
        mock_pipe.build.return_value = True
        mock_pipe.probe.return_value = True
        mock_pipe_cls.return_value = mock_pipe
        my_callback = MagicMock()

        fake = _FakeGstReceiver(info=_FakeInfo(), direct_audio=False,
                                recv_audio_callback=my_callback)
        fake._build_audio_pipeline()

        call = mock_pipe_cls.call_args
        passed_on_audio = call.kwargs.get('on_audio')
        self.assertIs(passed_on_audio, my_callback)

    @patch('robonet.gst_io.receiver_unencrypted._audio_decoder_candidates', return_value=['a', 'b'])
    @patch('robonet.gst_io.receiver_unencrypted._AudioRecvPipeline')
    def test_falls_through_to_next_decoder_on_build_failure(self, mock_pipe_cls, _candidates):
        failing = MagicMock()
        failing.build.return_value = False
        working = MagicMock()
        working.build.return_value = True
        working.probe.return_value = True
        mock_pipe_cls.side_effect = [failing, working]

        fake = _FakeGstReceiver(info=_FakeInfo())
        fake._build_audio_pipeline()

        working.play.assert_called_once()
        self.assertIs(fake._apipe, working)

    @patch('robonet.gst_io.receiver_unencrypted._audio_decoder_candidates', return_value=['a'])
    @patch('robonet.gst_io.receiver_unencrypted._AudioRecvPipeline')
    def test_all_decoders_failing_leaves_apipe_none(self, mock_pipe_cls, _candidates):
        failing = MagicMock()
        failing.build.return_value = False
        mock_pipe_cls.return_value = failing

        fake = _FakeGstReceiver(info=_FakeInfo())
        fake._build_audio_pipeline()

        self.assertIsNone(fake._apipe)


class TestSetDirectAudio(unittest.TestCase):

    @patch.object(_FakeGstReceiver, '_build_audio_pipeline')
    def test_no_op_when_nothing_changes(self, mock_build):
        fake = _FakeGstReceiver(direct_audio=True, audio_device='foo')
        fake.set_direct_audio(True, 'foo')
        mock_build.assert_not_called()

    @patch.object(_FakeGstReceiver, '_build_audio_pipeline')
    def test_updates_flag_and_rebuilds_on_direct_audio_toggle(self, mock_build):
        fake = _FakeGstReceiver(direct_audio=False, audio_device='default')
        fake.set_direct_audio(True, 'robonet_ai_in_playback')
        self.assertTrue(fake._direct_audio)
        self.assertEqual(fake._audio_device, 'robonet_ai_in_playback')
        mock_build.assert_called_once()

    @patch.object(_FakeGstReceiver, '_build_audio_pipeline')
    def test_device_only_change_still_rebuilds(self, mock_build):
        fake = _FakeGstReceiver(direct_audio=True, audio_device='old_device')
        fake.set_direct_audio(True, 'new_device')
        self.assertEqual(fake._audio_device, 'new_device')
        mock_build.assert_called_once()

    @patch.object(_FakeGstReceiver, '_build_audio_pipeline')
    def test_stops_existing_pipeline_before_rebuilding(self, mock_build):
        old_pipe = MagicMock()
        fake = _FakeGstReceiver(direct_audio=False)
        fake._apipe = old_pipe
        fake.set_direct_audio(True, 'robonet_ai_in_playback')
        old_pipe.stop.assert_called_once()

    @patch.object(_FakeGstReceiver, '_build_audio_pipeline')
    def test_audio_device_none_leaves_existing_device_unchanged(self, mock_build):
        fake = _FakeGstReceiver(direct_audio=False, audio_device='keep_me')
        fake.set_direct_audio(True)  # audio_device not given
        self.assertEqual(fake._audio_device, 'keep_me')
        self.assertTrue(fake._direct_audio)


class TestAudioRecvPipelineFormat(unittest.TestCase):
    """Regression test for a real bug: the caps filter used to omit
    format=, so audioconvert could (and did in practice) negotiate a
    different default like S16LE while _pull_chunk hardcoded np.float32
    when reading the raw bytes back -- 16-bit PCM reinterpreted as
    32-bit floats, producing garbage/NaN values."""

    @patch('robonet.gst_io.receiver_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.receiver_unencrypted.Gst.ElementFactory.make')
    def test_caps_explicitly_request_s16le(self, mock_make, _pipeline_new):
        created = {}

        def make(factory_name, elem_name):
            mock = MagicMock()
            created[factory_name] = mock
            return mock

        mock_make.side_effect = make

        info = MagicMock(audio_codec='opus', audio_port=5601, sample_rate=48000)
        from robonet.gst_io.receiver_unencrypted import _AudioRecvPipeline
        pipe = _AudioRecvPipeline(info, 'opusdec', direct_audio=False,
                                  audio_device='default', on_audio=MagicMock())

        pipe.build()

        caps_calls = [c for c in created['capsfilter'].set_property.call_args_list
                     if c.args[0] == 'caps']
        self.assertEqual(len(caps_calls), 1)
        caps_str = caps_calls[0].args[1].to_string()
        self.assertIn('S16LE', caps_str)


if __name__ == '__main__':
    unittest.main()


class TestLocalPlaybackTee(unittest.TestCase):
    """play_locally tees the receive pipeline into both the S16LE
    appsink (numpy) branch and an autoaudiosink playback branch --
    brain-side playback now lives entirely in GStreamer, whose threads
    are unaffected by the display loop's vsync blocking (which starved
    the old realtime sounddevice callback into constant underruns)."""

    def _build(self, direct_audio=False, play_locally=False):
        from robonet.gst_io.receiver_unencrypted import _AudioRecvPipeline
        created = {}

        def make(factory_name, elem_name):
            mock = MagicMock()
            created.setdefault(factory_name, []).append(mock)
            return mock

        with patch('robonet.gst_io.receiver_unencrypted.Gst.Pipeline.new', return_value=MagicMock()), \
             patch('robonet.gst_io.receiver_unencrypted.Gst.ElementFactory.make', side_effect=make):
            info = MagicMock(audio_codec='opus', audio_port=5601, sample_rate=48000)
            pipe = _AudioRecvPipeline(info, 'opusdec', direct_audio=direct_audio,
                                      audio_device='default', on_audio=MagicMock(),
                                      play_locally=play_locally)
            ok = pipe.build()
        return ok, created

    def test_play_locally_creates_the_tee_and_playback_sink(self):
        ok, created = self._build(direct_audio=False, play_locally=True)
        self.assertTrue(ok)
        self.assertIn('tee', created)
        self.assertIn('autoaudiosink', created)
        self.assertIn('appsink', created)  # numpy branch still present

    def test_no_play_locally_means_no_tee(self):
        ok, created = self._build(direct_audio=False, play_locally=False)
        self.assertTrue(ok)
        self.assertNotIn('tee', created)
        self.assertIn('appsink', created)

    def test_direct_audio_endpoint_ignores_play_locally(self):
        # The endpoint already plays directly through its own sink --
        # a second playback branch there would be double audio.
        ok, created = self._build(direct_audio=True, play_locally=True)
        self.assertTrue(ok)
        self.assertNotIn('tee', created)
        self.assertNotIn('appsink', created)


class TestPullChunkS16Conversion(unittest.TestCase):
    """_pull_chunk must read the wire's actual format (S16LE) and hand
    every consumer float32 in [-1, 1]. Reading the wrong width gives
    exactly-2x or exactly-half sample counts -- as measured on real
    hardware when the layers disagreed."""

    def _pull(self, int16_samples):
        from robonet.gst_io.receiver_unencrypted import _AudioRecvPipeline
        received = []
        pipe = _AudioRecvPipeline.__new__(_AudioRecvPipeline)
        pipe._on_audio = received.append

        mapinfo = MagicMock()
        mapinfo.data = np.asarray(int16_samples, dtype=np.int16).tobytes()
        buf = MagicMock()
        buf.map.return_value = (True, mapinfo)
        sample = MagicMock()
        sample.get_buffer.return_value = buf
        sink = MagicMock()
        sink.emit.return_value = sample

        pipe._pull_chunk(sink)
        return received[0]

    def test_sample_count_matches_the_s16_wire_format(self):
        chunk = self._pull([0, 100, -100, 32767])
        self.assertEqual(len(chunk), 4)  # not 2 (F32-read bug) and not 8

    def test_values_arrive_as_float32_in_minus_one_to_one(self):
        chunk = self._pull([0, 16384, -16384, 32767])
        self.assertEqual(chunk.dtype, np.float32)
        np.testing.assert_allclose(chunk, [0.0, 0.5, -0.5, 32767 / 32768.0], atol=1e-6)
