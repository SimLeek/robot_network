"""
tests/test_ai_audio.py

Tests robonet/brain/ai_audio.py:
  - _PwLoopback's subprocess lifecycle (mocked subprocess.Popen -- no
    real PipeWire server needed).
  - setup_ai_audio_input/output: correct redirection calls onto
    GstReceiver.set_direct_audio / GstSender.set_mic_device, correct
    sounddevice stream construction, and the callback-wrapping behavior
    (shape handling, exceptions never escaping into the audio thread).

sounddevice.InputStream/OutputStream are mocked throughout -- opening a
real stream needs an actual PipeWire node to exist, which needs a live
audio server this environment doesn't have. The wrapping logic around
them (what gets called, with what, and how errors are handled) is what's
under test, not PortAudio itself.
"""

import unittest
from unittest.mock import patch, MagicMock, call

import numpy as np

import robonet.brain.ai_audio as ai_audio
from robonet.audio_io import audio_neuron_spec


class TestPwLoopback(unittest.TestCase):

    @patch('robonet.brain.ai_audio.subprocess.Popen')
    def test_start_builds_expected_command(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running -- looks healthy
        mock_popen.return_value = mock_proc

        loopback = ai_audio._PwLoopback('robonet_test')
        ok = loopback.start()

        self.assertTrue(ok)
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], 'pw-loopback')
        self.assertIn('node.name=robonet_test_capture', ' '.join(cmd))
        self.assertIn('node.name=robonet_test_playback', ' '.join(cmd))

    def test_node_names_derived_from_base_name(self):
        loopback = ai_audio._PwLoopback('foo')
        self.assertEqual(loopback.capture_node, 'foo_capture')
        self.assertEqual(loopback.playback_node, 'foo_playback')

    @patch('robonet.brain.ai_audio.subprocess.Popen', side_effect=FileNotFoundError())
    def test_start_returns_false_when_pw_loopback_missing(self, _popen):
        loopback = ai_audio._PwLoopback('robonet_test')
        self.assertFalse(loopback.start())

    @patch('robonet.brain.ai_audio.subprocess.Popen')
    def test_start_returns_false_when_process_exits_immediately(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # already exited -- something went wrong
        mock_proc.stderr.read.return_value = b'pipewire not running'
        mock_popen.return_value = mock_proc

        loopback = ai_audio._PwLoopback('robonet_test')
        self.assertFalse(loopback.start())

    def test_stop_before_start_does_not_raise(self):
        loopback = ai_audio._PwLoopback('robonet_test')
        loopback.stop()  # should be a no-op

    @patch('robonet.brain.ai_audio.subprocess.Popen')
    def test_stop_terminates_running_process(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running, both at start() and stop()
        mock_popen.return_value = mock_proc
        loopback = ai_audio._PwLoopback('robonet_test')
        loopback.start()

        loopback.stop()

        mock_proc.terminate.assert_called_once()

    @patch('robonet.brain.ai_audio.subprocess.Popen')
    def test_stop_kills_if_terminate_does_not_finish_in_time(self, mock_popen):
        import subprocess as _sp
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # never reports exited
        mock_proc.wait.side_effect = _sp.TimeoutExpired(cmd='pw-loopback', timeout=2)
        mock_popen.return_value = mock_proc
        loopback = ai_audio._PwLoopback('robonet_test')
        loopback.start()

        loopback.stop()

        mock_proc.kill.assert_called_once()


class TestAudioIOHandle(unittest.TestCase):

    def test_stop_stops_and_closes_stream_then_loopback(self):
        loopback = MagicMock()
        stream = MagicMock()
        handle = ai_audio.AudioIOHandle(loopback, stream, audio_neuron_spec(48000, 480))

        handle.stop()

        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        loopback.stop.assert_called_once()

    def test_stop_tolerates_stream_errors_and_still_stops_loopback(self):
        loopback = MagicMock()
        stream = MagicMock()
        stream.stop.side_effect = Exception('already closed')
        handle = ai_audio.AudioIOHandle(loopback, stream, audio_neuron_spec(48000, 480))

        handle.stop()  # must not raise

        loopback.stop.assert_called_once()

    def test_neuron_spec_exposed(self):
        handle = ai_audio.AudioIOHandle(MagicMock(), MagicMock(), audio_neuron_spec(48000, 480))
        self.assertEqual(handle.neuron_spec.count, 480)
        self.assertEqual(handle.neuron_spec.hz, 100.0)


def _make_fake_sm():
    sm = MagicMock()
    return sm


class TestSetupAiAudioInput(unittest.TestCase):

    def _patch_loopback_ok(self):
        return patch.object(ai_audio, '_PwLoopback')

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_redirects_gst_receiver_to_loopback_playback_node(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'robonet_ai_in_playback'
            fake_lb.capture_node = 'robonet_ai_in_capture'
            mock_loopback_cls.return_value = fake_lb

            handle = ai_audio.setup_ai_audio_input(sm, callback=lambda block: None)

        sm.menu.gst_receiver.set_direct_audio.assert_called_once_with(True, 'robonet_ai_in_playback')
        self.assertIsNotNone(handle)

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_opens_input_stream_against_capture_node_with_requested_sizing(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_input(sm, callback=lambda block: None,
                                          sample_rate=16000, blocksize=160)

        _, kwargs = mock_sd.InputStream.call_args
        self.assertEqual(kwargs['device'], 'x_capture')
        self.assertEqual(kwargs['samplerate'], 16000)
        self.assertEqual(kwargs['blocksize'], 160)
        self.assertEqual(kwargs['channels'], 1)

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_returns_none_and_cleans_up_when_loopback_fails(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = False
            mock_loopback_cls.return_value = fake_lb

            result = ai_audio.setup_ai_audio_input(sm, callback=lambda block: None)

        self.assertIsNone(result)
        sm.menu.gst_receiver.set_direct_audio.assert_not_called()

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_returns_none_and_stops_loopback_when_stream_open_fails(self, mock_sd):
        mock_sd.InputStream.side_effect = Exception('device busy')
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            result = ai_audio.setup_ai_audio_input(sm, callback=lambda block: None)

        self.assertIsNone(result)
        fake_lb.stop.assert_called_once()

    @patch('robonet.brain.ai_audio.sounddevice', None)
    def test_raises_clear_error_when_sounddevice_unavailable(self):
        sm = _make_fake_sm()
        with self.assertRaises(ai_audio.AudioIOError):
            ai_audio.setup_ai_audio_input(sm, callback=lambda block: None)

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_callback_wrapper_extracts_mono_and_copies(self, mock_sd):
        sm = _make_fake_sm()
        received = []
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_input(sm, callback=lambda block: received.append(block))

        internal_callback = mock_sd.InputStream.call_args.kwargs['callback']
        raw = np.array([[0.1], [0.2], [0.3]], dtype='float32')
        internal_callback(raw, 3, None, None)

        self.assertEqual(len(received), 1)
        np.testing.assert_array_almost_equal(received[0], [0.1, 0.2, 0.3])
        # mutating the original buffer afterwards must not affect what was delivered
        raw[:] = 0
        self.assertNotEqual(received[0][0], 0.0)

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_callback_exception_does_not_propagate(self, mock_sd):
        sm = _make_fake_sm()
        def boom(block):
            raise RuntimeError('AI blew up')
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_input(sm, callback=boom)

        internal_callback = mock_sd.InputStream.call_args.kwargs['callback']
        raw = np.zeros((3, 1), dtype='float32')
        try:
            internal_callback(raw, 3, None, None)  # must not raise
        except Exception as e:
            self.fail(f'internal callback let an exception escape: {e}')


class TestSetupAiAudioOutput(unittest.TestCase):

    def _patch_loopback_ok(self):
        return patch.object(ai_audio, '_PwLoopback')

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_redirects_gst_sender_to_loopback_capture_node(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            handle = ai_audio.setup_ai_audio_output(sm, callback=lambda frames: np.zeros(frames))

        sm.menu.gst_sender.set_mic_device.assert_called_once_with('x_capture')
        self.assertIsNotNone(handle)

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_opens_output_stream_against_playback_node(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_output(sm, callback=lambda frames: np.zeros(frames),
                                           sample_rate=44100, blocksize=1024)

        _, kwargs = mock_sd.OutputStream.call_args
        self.assertEqual(kwargs['device'], 'x_playback')
        self.assertEqual(kwargs['samplerate'], 44100)
        self.assertEqual(kwargs['blocksize'], 1024)

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_callback_wrapper_writes_exact_length_block(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_output(sm, callback=lambda frames: np.full(frames, 0.5))

        internal_callback = mock_sd.OutputStream.call_args.kwargs['callback']
        outdata = np.zeros((4, 1), dtype='float32')
        internal_callback(outdata, 4, None, None)

        np.testing.assert_array_almost_equal(outdata[:, 0], [0.5, 0.5, 0.5, 0.5])

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_callback_wrapper_pads_short_block(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_output(sm, callback=lambda frames: np.ones(2))  # short

        internal_callback = mock_sd.OutputStream.call_args.kwargs['callback']
        outdata = np.zeros((5, 1), dtype='float32')
        internal_callback(outdata, 5, None, None)

        np.testing.assert_array_almost_equal(outdata[:, 0], [1.0, 1.0, 0.0, 0.0, 0.0])

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_callback_wrapper_truncates_long_block(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_output(sm, callback=lambda frames: np.ones(10))  # too long

        internal_callback = mock_sd.OutputStream.call_args.kwargs['callback']
        outdata = np.zeros((3, 1), dtype='float32')
        internal_callback(outdata, 3, None, None)

        self.assertEqual(outdata.shape, (3, 1))
        np.testing.assert_array_almost_equal(outdata[:, 0], [1.0, 1.0, 1.0])

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_callback_exception_produces_silence_not_a_crash(self, mock_sd):
        sm = _make_fake_sm()
        def boom(frames):
            raise RuntimeError('AI blew up')
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = True
            fake_lb.playback_node = 'x_playback'
            fake_lb.capture_node = 'x_capture'
            mock_loopback_cls.return_value = fake_lb

            ai_audio.setup_ai_audio_output(sm, callback=boom)

        internal_callback = mock_sd.OutputStream.call_args.kwargs['callback']
        outdata = np.full((4, 1), 9.0, dtype='float32')
        internal_callback(outdata, 4, None, None)  # must not raise

        np.testing.assert_array_almost_equal(outdata[:, 0], [0.0, 0.0, 0.0, 0.0])

    @patch('robonet.brain.ai_audio.sounddevice')
    def test_returns_none_when_loopback_fails(self, mock_sd):
        sm = _make_fake_sm()
        with self._patch_loopback_ok() as mock_loopback_cls:
            fake_lb = MagicMock()
            fake_lb.start.return_value = False
            mock_loopback_cls.return_value = fake_lb

            result = ai_audio.setup_ai_audio_output(sm, callback=lambda frames: np.zeros(frames))

        self.assertIsNone(result)
        sm.menu.gst_sender.set_mic_device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
