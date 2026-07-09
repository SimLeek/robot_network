"""
tests/test_desktop_capture.py

Tests robonet/endpoint/desktop_capture.py:
  - Device discovery (find_v4l2loopback_device, find_alsa_loopback_card_index,
    ensure_*, find_real_webcam) against a mocked filesystem -- no real
    v4l2loopback/snd-aloop devices needed.
  - DesktopVideoFeeder/DesktopAudioFeeder pipeline construction against a
    mocked Gst.parse_launch, asserting on the actual pipeline description
    string built rather than on whether specific GStreamer plugins happen
    to be installed on whatever machine runs the tests (a portability/
    environment concern, not a logic concern).
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, mock_open, MagicMock

import numpy as np

from robonet.endpoint import desktop_capture as dc


class TestFindV4l2loopbackDevice(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.glob.glob')
    def test_finds_matching_label(self, mock_glob):
        mock_glob.return_value = [
            '/sys/class/video4linux/video0/name',
            '/sys/class/video4linux/video42/name',
        ]
        contents = {
            '/sys/class/video4linux/video0/name': 'Integrated Webcam\n',
            '/sys/class/video4linux/video42/name': 'RobonetDesktopCam\n',
        }
        with patch('builtins.open', side_effect=lambda p, *a, **kw: mock_open(read_data=contents[p]).return_value):
            result = dc.find_v4l2loopback_device()
        self.assertEqual(result, '/dev/video42')

    @patch('robonet.endpoint.desktop_capture.glob.glob')
    def test_returns_none_when_not_found(self, mock_glob):
        mock_glob.return_value = ['/sys/class/video4linux/video0/name']
        with patch('builtins.open', mock_open(read_data='Integrated Webcam\n')):
            result = dc.find_v4l2loopback_device()
        self.assertIsNone(result)

    @patch('robonet.endpoint.desktop_capture.glob.glob')
    def test_unreadable_entry_is_skipped_not_fatal(self, mock_glob):
        mock_glob.return_value = [
            '/sys/class/video4linux/video0/name',
            '/sys/class/video4linux/video42/name',
        ]
        def fake_open(path, *a, **kw):
            if path.endswith('video0/name'):
                raise OSError('permission denied')
            return mock_open(read_data='RobonetDesktopCam\n').return_value
        with patch('builtins.open', side_effect=fake_open):
            result = dc.find_v4l2loopback_device()
        self.assertEqual(result, '/dev/video42')

    def test_custom_label_respected(self):
        with patch('robonet.endpoint.desktop_capture.glob.glob',
                  return_value=['/sys/class/video4linux/video7/name']), \
             patch('builtins.open', mock_open(read_data='SomeOtherCam\n')):
            self.assertEqual(dc.find_v4l2loopback_device(label='SomeOtherCam'), '/dev/video7')


class TestEnsureV4l2loopbackDevice(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.find_v4l2loopback_device')
    def test_returns_device_when_found(self, mock_find):
        mock_find.return_value = '/dev/video42'
        self.assertEqual(dc.ensure_v4l2loopback_device(), '/dev/video42')

    @patch('robonet.endpoint.desktop_capture.find_v4l2loopback_device')
    def test_raises_actionable_error_when_missing(self, mock_find):
        mock_find.return_value = None
        with self.assertRaises(dc.DesktopCaptureError) as ctx:
            dc.ensure_v4l2loopback_device()
        self.assertIn('setup_desktop_capture.sh', str(ctx.exception))


class TestFindAlsaLoopbackCardIndex(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.os.path.exists', return_value=True)
    def test_finds_loopback_card(self, _exists):
        cards = ' 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n' \
               ' 1 [Loopback       ]: Loopback - Loopback\n'
        with patch('builtins.open', mock_open(read_data=cards)):
            self.assertEqual(dc.find_alsa_loopback_card_index(), 1)

    @patch('robonet.endpoint.desktop_capture.os.path.exists', return_value=True)
    def test_returns_none_when_no_loopback_card(self, _exists):
        cards = ' 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n'
        with patch('builtins.open', mock_open(read_data=cards)):
            self.assertIsNone(dc.find_alsa_loopback_card_index())

    @patch('robonet.endpoint.desktop_capture.os.path.exists', return_value=False)
    def test_returns_none_when_proc_asound_missing(self, _exists):
        self.assertIsNone(dc.find_alsa_loopback_card_index())


class TestEnsureAlsaLoopback(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.find_alsa_loopback_card_index', return_value=1)
    def test_returns_correct_playback_and_capture_pair(self, _idx):
        playback, capture = dc.ensure_alsa_loopback()
        self.assertEqual(playback, 'hw:1,0,0')
        self.assertEqual(capture, 'hw:1,1,0')

    @patch('robonet.endpoint.desktop_capture.find_alsa_loopback_card_index', return_value=None)
    def test_raises_actionable_error_when_missing(self, _idx):
        with self.assertRaises(dc.DesktopCaptureError) as ctx:
            dc.ensure_alsa_loopback()
        self.assertIn('setup_desktop_capture.sh', str(ctx.exception))


class TestFindRealWebcam(unittest.TestCase):

    @patch('robonet.gst_io.devices.find_camera_devices')
    def test_excludes_own_loopback_device(self, mock_find):
        mock_find.return_value = ['/dev/video42', '/dev/video0']
        self.assertEqual(dc.find_real_webcam(exclude_device='/dev/video42'), '/dev/video0')

    @patch('robonet.gst_io.devices.find_camera_devices')
    def test_returns_none_when_only_loopback_present(self, mock_find):
        mock_find.return_value = ['/dev/video42']
        self.assertIsNone(dc.find_real_webcam(exclude_device='/dev/video42'))

    @patch('robonet.gst_io.devices.find_camera_devices')
    def test_returns_none_when_no_cameras_at_all(self, mock_find):
        mock_find.return_value = []
        self.assertIsNone(dc.find_real_webcam(exclude_device='/dev/video42'))


class TestDesktopVideoFeederBuild(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_pipeline_string_uses_configured_device_and_resolution(self, mock_parse):
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopVideoFeeder('/dev/video42', width=1280, height=720, fps=15)

        ok = feeder.build()

        self.assertTrue(ok)
        desc = mock_parse.call_args[0][0]
        self.assertIn('device=/dev/video42', desc)
        self.assertIn('width=1280', desc)
        self.assertIn('height=720', desc)
        self.assertIn('framerate=15/1', desc)
        self.assertIn('ximagesrc', desc)
        self.assertIn('v4l2sink', desc)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_build_failure_returns_false_not_raise(self, mock_parse):
        mock_parse.side_effect = dc.GLib.Error('bad pipeline')
        feeder = dc.DesktopVideoFeeder('/dev/video42')

        self.assertFalse(feeder.build())

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_start_builds_if_not_already_built(self, mock_parse):
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.SUCCESS
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopVideoFeeder('/dev/video42')

        with patch('robonet.endpoint.desktop_capture.GLib.MainLoop') as mock_loop_cls, \
             patch('robonet.endpoint.desktop_capture.threading.Thread') as mock_thread_cls:
            mock_loop_cls.return_value = MagicMock()
            mock_thread_cls.return_value = MagicMock()
            ok = feeder.start()

        self.assertTrue(ok)
        mock_parse.assert_called_once()
        fake_pipeline.set_state.assert_called_with(dc.Gst.State.PLAYING)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_start_returns_false_when_state_change_fails(self, mock_parse):
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.FAILURE
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopVideoFeeder('/dev/video42')

        self.assertFalse(feeder.start())

    def test_stop_before_start_does_not_raise(self):
        feeder = dc.DesktopVideoFeeder('/dev/video42')
        feeder.stop()  # should be a no-op, not an AttributeError


class TestDesktopAudioFeederBuild(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_mixes_monitor_and_distinct_mic(self, mock_pactl, mock_parse):
        mock_pactl.side_effect = lambda field: 'alsa_output.pci-0000.sink' if field == 'sink' else 'alsa_input.usb-mic'
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopAudioFeeder('hw:1,0,0', include_mic=True)

        ok = feeder.build()

        self.assertTrue(ok)
        desc = mock_parse.call_args[0][0]
        self.assertIn('alsa_output.pci-0000.sink.monitor', desc)
        self.assertIn('alsa_input.usb-mic', desc)
        self.assertIn('alsasink device=hw:1,0,0', desc)
        # two distinct pulsesrc branches feeding the mixer
        self.assertEqual(desc.count('pulsesrc'), 2)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_skips_mic_when_it_equals_monitor(self, mock_pactl, mock_parse):
        # e.g. default source somehow resolves to the same device as the monitor
        mock_pactl.side_effect = lambda field: 'same_device' if field == 'sink' else 'same_device.monitor'
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopAudioFeeder('hw:1,0,0', include_mic=True)

        feeder.build()

        desc = mock_parse.call_args[0][0]
        self.assertEqual(desc.count('pulsesrc'), 1)

    @patch.object(dc.DesktopAudioFeeder, '_pactl_get', return_value=None)
    def test_no_sink_or_source_disables_gracefully_no_raise(self, _pactl):
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')

        ok = feeder.build()

        self.assertFalse(ok)  # degrades gracefully, does not raise

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_include_mic_false_only_uses_monitor(self, mock_pactl, mock_parse):
        mock_pactl.return_value = 'some_device'
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopAudioFeeder('hw:1,0,0', include_mic=False)

        feeder.build()

        desc = mock_parse.call_args[0][0]
        self.assertEqual(desc.count('pulsesrc'), 1)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_start_builds_if_not_already_built(self, mock_pactl, mock_parse):
        mock_pactl.return_value = 'some_device'
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.SUCCESS
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')

        with patch('robonet.endpoint.desktop_capture.GLib.MainLoop') as mock_loop_cls, \
             patch('robonet.endpoint.desktop_capture.threading.Thread') as mock_thread_cls:
            mock_loop_cls.return_value = MagicMock()
            mock_thread_cls.return_value = MagicMock()
            ok = feeder.start()

        self.assertTrue(ok)
        fake_pipeline.set_state.assert_called_with(dc.Gst.State.PLAYING)

    @patch.object(dc.DesktopAudioFeeder, '_pactl_get', return_value=None)
    def test_start_returns_false_when_build_fails(self, _pactl):
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')
        self.assertFalse(feeder.start())

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_start_returns_false_when_state_change_fails(self, mock_pactl, mock_parse):
        mock_pactl.return_value = 'some_device'
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.FAILURE
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')

        self.assertFalse(feeder.start())

    def test_stop_before_start_does_not_raise(self):
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')
        feeder.stop()

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_stop_after_start_tears_down_pipeline_and_loop(self, mock_pactl, mock_parse):
        mock_pactl.return_value = 'some_device'
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.SUCCESS
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')
        fake_loop = MagicMock()
        fake_loop.is_running.return_value = True
        with patch('robonet.endpoint.desktop_capture.GLib.MainLoop', return_value=fake_loop), \
             patch('robonet.endpoint.desktop_capture.threading.Thread', return_value=MagicMock()):
            feeder.start()

        feeder.stop()

        fake_pipeline.set_state.assert_called_with(dc.Gst.State.NULL)
        fake_loop.quit.assert_called_once()
        self.assertIsNone(feeder._pipeline)
        self.assertIsNone(feeder._glib_loop)


class _FakeGstSenderForSourceSwitch:
    """Minimal GstSender stand-in for set_source_device -- just the
    attributes that method actually touches."""
    def __init__(self, src_device, acked=True, receiver_ip='10.0.0.1', has_candidates=True):
        self._src_device = src_device
        self._acked = acked
        self._receiver_ip = receiver_ip
        self._video_candidates = ['h264'] if has_candidates else []
        self._vpipe = None
        self.started_pipelines = []

    def _start_video_pipeline(self):
        pipe = MagicMock(name=f'pipeline-for-{self._src_device}')
        self.started_pipelines.append((self._src_device, pipe))
        return pipe


class _FakeGstSenderForMicSwitch:
    """Minimal GstSender stand-in for set_mic_device."""
    def __init__(self, mic_device, acked=True, receiver_ip='10.0.0.1', has_candidates=True):
        self._mic_device = mic_device
        self._acked = acked
        self._receiver_ip = receiver_ip
        self._audio_candidates = ['opus'] if has_candidates else []
        self._apipe = None
        self.started_pipelines = []

    def _start_audio_pipeline(self):
        pipe = MagicMock(name=f'audio-pipeline-for-{self._mic_device}')
        self.started_pipelines.append((self._mic_device, pipe))
        return pipe


class TestGstSenderSetMicDevice(unittest.TestCase):
    """GstSender.set_mic_device -- the audio-channel sibling of
    set_source_device, needed to redirect the brain's mic source from a
    real human mic to an AI-driven PipeWire virtual device."""

    def _bind(self, fake):
        from robonet.gst_io.streamer_unencrypted import GstSender
        return GstSender.set_mic_device.__get__(fake)

    def test_same_device_is_a_no_op(self):
        fake = _FakeGstSenderForMicSwitch('default')
        self._bind(fake)('default')
        self.assertEqual(fake.started_pipelines, [])

    def test_not_yet_streaming_just_stores_device(self):
        fake = _FakeGstSenderForMicSwitch('default', acked=False)
        self._bind(fake)('robonet_ai_out_capture')
        self.assertEqual(fake._mic_device, 'robonet_ai_out_capture')
        self.assertEqual(fake.started_pipelines, [])

    def test_streaming_switches_device_and_restarts_audio_pipeline(self):
        old_pipe = MagicMock()
        fake = _FakeGstSenderForMicSwitch('default', acked=True)
        fake._apipe = old_pipe
        self._bind(fake)('robonet_ai_out_capture')

        old_pipe.stop.assert_called_once()
        self.assertEqual(fake._mic_device, 'robonet_ai_out_capture')
        self.assertEqual(len(fake.started_pipelines), 1)
        self.assertIsNotNone(fake._apipe)

    def test_does_not_touch_video_state(self):
        fake = _FakeGstSenderForMicSwitch('default', acked=True)
        fake._src_device = '/dev/video0'  # unrelated video state
        self._bind(fake)('robonet_ai_out_capture')
        self.assertEqual(fake._src_device, '/dev/video0')  # untouched


class TestGstSenderSetSourceDevice(unittest.TestCase):
    """robonet/gst_io/streamer_unencrypted.py's GstSender.set_source_device,
    the mechanism behind the desktop-mode camera/desktop-capture switch."""

    def _bind(self, fake):
        from robonet.gst_io.streamer_unencrypted import GstSender
        return GstSender.set_source_device.__get__(fake)

    def test_same_device_is_a_no_op(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42')
        set_source_device = self._bind(fake)

        set_source_device('/dev/video42')

        self.assertEqual(fake.started_pipelines, [])

    def test_not_yet_streaming_just_stores_device_no_pipeline_restart(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=False)
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        self.assertEqual(fake._src_device, '/dev/video0')
        self.assertEqual(fake.started_pipelines, [])

    def test_streaming_switches_device_and_restarts_pipeline(self):
        old_pipe = MagicMock()
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=True)
        fake._vpipe = old_pipe
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        old_pipe.stop.assert_called_once()
        self.assertEqual(fake._src_device, '/dev/video0')
        self.assertEqual(len(fake.started_pipelines), 1)
        self.assertEqual(fake.started_pipelines[0][0], '/dev/video0')
        self.assertIsNotNone(fake._vpipe)

    def test_streaming_but_no_prior_pipeline_still_starts_new_one(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=True)
        fake._vpipe = None  # e.g. video encoder never successfully started before
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        self.assertEqual(len(fake.started_pipelines), 1)

    def test_no_receiver_ip_does_not_attempt_pipeline_start(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=True, receiver_ip=None)
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        self.assertEqual(fake.started_pipelines, [])
        self.assertEqual(fake._src_device, '/dev/video0')  # device is still updated though


class TestDesktopHwConstruction(unittest.TestCase):
    """DesktopHw's __init__ calls into MultiAVRobotHardware.__init__,
    which needs a real psk key file to exist (endpoint/settings.py has
    no advanced-settings restriction, unlike the brain side, so we can
    just point it at a temp one directly and restore it after)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='robonet_desktop_hw_test_')
        self.psk_path = os.path.join(self.tmpdir, 'psk.key')
        with open(self.psk_path, 'wb') as f:
            f.write(os.urandom(32))
        import robonet.endpoint.settings as settings_
        self._settings = settings_.get()
        self._orig_psk = self._settings['psk_file']
        self._settings['psk_file'] = self.psk_path

    def tearDown(self):
        self._settings['psk_file'] = self._orig_psk
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _construct(self, **kwargs):
        with patch('robonet.endpoint.desktop_hardware.pyautogui', MagicMock()), \
             patch('robonet.endpoint.desktop_hardware.ensure_v4l2loopback_device',
                  return_value='/dev/video42'), \
             patch('robonet.endpoint.desktop_hardware.ensure_alsa_loopback',
                  return_value=('hw:1,0,0', 'hw:1,1,0')), \
             patch('robonet.endpoint.desktop_hardware.find_real_webcam', return_value=None), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device',
                  return_value='hw:0,0'):
            from robonet.endpoint.desktop_hardware import DesktopHw
            return DesktopHw(**kwargs)

    def test_construction_succeeds_and_wires_devices(self):
        hw = self._construct()
        self.assertEqual(hw._video_sources['desktop'], '/dev/video42')
        self.assertEqual(hw._active_video, 'desktop')
        self.assertNotIn('webcam', str(list(hw._video_sources)))  # no webcam entry when none found

    def test_construction_raises_clear_error_when_pyautogui_unavailable(self):
        with patch('robonet.endpoint.desktop_hardware.pyautogui', None), \
             patch('robonet.endpoint.desktop_hardware._PYAUTOGUI_IMPORT_ERROR', KeyError('DISPLAY')), \
             patch('robonet.endpoint.desktop_hardware.ensure_v4l2loopback_device',
                  return_value='/dev/video42'), \
             patch('robonet.endpoint.desktop_hardware.ensure_alsa_loopback',
                  return_value=('hw:1,0,0', 'hw:1,1,0')):
            from robonet.endpoint.desktop_hardware import DesktopHw, DesktopCaptureError
            with self.assertRaises(DesktopCaptureError) as ctx:
                DesktopHw()
            self.assertIn('DISPLAY', str(ctx.exception))

    def test_pyautogui_unavailable_error_path_does_not_itself_crash(self):
        """Regression test: _PYAUTOGUI_IMPORT_ERROR previously had no
        default value, so if pyautogui was None for any reason other
        than the module's own except block having just run (e.g. patched
        to None without also patching the error variable, which is
        exactly what happens in a real process where the import
        succeeded and something else set it to None later), building the
        error message raised NameError instead of the intended
        DesktopCaptureError. Only patches pyautogui itself here, not the
        error variable, to catch that gap if it comes back."""
        with patch('robonet.endpoint.desktop_hardware.pyautogui', None), \
             patch('robonet.endpoint.desktop_hardware.ensure_v4l2loopback_device',
                  return_value='/dev/video42'), \
             patch('robonet.endpoint.desktop_hardware.ensure_alsa_loopback',
                  return_value=('hw:1,0,0', 'hw:1,1,0')):
            from robonet.endpoint.desktop_hardware import DesktopHw, DesktopCaptureError
            try:
                DesktopHw()
                self.fail('expected DesktopCaptureError')
            except DesktopCaptureError:
                pass  # correct
            except NameError as e:
                self.fail(f'_PYAUTOGUI_IMPORT_ERROR regression: {e}')

    def test_construction_propagates_missing_v4l2loopback(self):
        from robonet.endpoint.desktop_capture import DesktopCaptureError as CaptureErr
        with patch('robonet.endpoint.desktop_hardware.pyautogui', MagicMock()), \
             patch('robonet.endpoint.desktop_hardware.ensure_v4l2loopback_device',
                  side_effect=CaptureErr('no loopback device')):
            from robonet.endpoint.desktop_hardware import DesktopHw
            with self.assertRaises(CaptureErr):
                DesktopHw()

    def test_real_webcam_detected_when_present(self):
        with patch('robonet.endpoint.desktop_hardware.pyautogui', MagicMock()), \
             patch('robonet.endpoint.desktop_hardware.ensure_v4l2loopback_device',
                  return_value='/dev/video42'), \
             patch('robonet.endpoint.desktop_hardware.ensure_alsa_loopback',
                  return_value=('hw:1,0,0', 'hw:1,1,0')), \
             patch('robonet.endpoint.desktop_hardware.find_real_webcam', return_value='/dev/video0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device',
                  return_value='hw:0,0'):
            from robonet.endpoint.desktop_hardware import DesktopHw
            hw = DesktopHw()
        self.assertIn('webcam:/dev/video0', hw._video_sources)
        self.assertEqual(hw._video_sources['webcam:/dev/video0'], '/dev/video0')

    def test_no_speaker_found_degrades_gracefully(self):
        from robonet.gst_io.devices import DeviceNotFoundError
        with patch('robonet.endpoint.desktop_hardware.pyautogui', MagicMock()), \
             patch('robonet.endpoint.desktop_hardware.ensure_v4l2loopback_device',
                  return_value='/dev/video42'), \
             patch('robonet.endpoint.desktop_hardware.ensure_alsa_loopback',
                  return_value=('hw:1,0,0', 'hw:1,1,0')), \
             patch('robonet.endpoint.desktop_hardware.find_real_webcam', return_value=None), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device',
                  side_effect=DeviceNotFoundError('no speaker')):
            from robonet.endpoint.desktop_hardware import DesktopHw
            hw = DesktopHw()  # must not raise
        self.assertEqual(hw._audio_outputs, {})

    def test_build_capabilities_matches_module_function(self):
        hw = self._construct()
        caps = hw.build_capabilities()
        self.assertEqual(caps.endpoint_type, 'desktop')
        self.assertEqual(caps.axes(), [])

    def test_handlers_include_desktop_specific_and_inherited(self):
        hw = self._construct()
        handlers = hw.handlers
        self.assertIn('KeyEvent', handlers)
        self.assertIn('MouseEvent', handlers)
        self.assertIn('SelectAVSource', handlers)
        self.assertIn('SparseVectorBuffer', handlers)  # inherited from RobotHardware

    def test_apply_tensor_with_entries_does_not_raise(self):
        hw = self._construct()
        hw.apply_tensor(np.array([1, 2]), np.array([0.5, 0.5]))  # should just log, not crash

    def test_apply_tensor_empty_does_not_raise(self):
        hw = self._construct()
        hw.apply_tensor(np.array([]), np.array([]))

    def test_setup_starts_feeders(self):
        hw = self._construct()
        hw._video_feeder = MagicMock()
        hw._audio_feeder = MagicMock()

        hw.setup(MagicMock())

        hw._video_feeder.start.assert_called_once()
        hw._audio_feeder.start.assert_called_once()

    def test_stop_releases_held_input_and_stops_feeders(self):
        hw = self._construct()
        hw._video_feeder = MagicMock()
        hw._audio_feeder = MagicMock()
        hw._held_keys = {'a'}
        hw._held_buttons = {'left'}
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pag:
            hw.stop()
            mock_pag.keyUp.assert_called_once_with('a')
            mock_pag.mouseUp.assert_called_once_with(button='left')
        hw._video_feeder.stop.assert_called_once()
        hw._audio_feeder.stop.assert_called_once()
        self.assertEqual(hw._held_keys, set())
        self.assertEqual(hw._held_buttons, set())

    def test_halt_releases_held_input_without_stopping_feeders(self):
        hw = self._construct()
        hw._video_feeder = MagicMock()
        hw._held_keys = {'shift'}
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pag:
            hw.halt()
            mock_pag.keyUp.assert_called_once_with('shift')
        hw._video_feeder.stop.assert_not_called()  # halt is not a full stop

    def test_release_all_held_swallows_pyautogui_errors(self):
        hw = self._construct()
        hw._held_keys = {'a', 'b'}
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pag:
            mock_pag.keyUp.side_effect = Exception('X server gone')
            hw._release_all_held()  # must not raise
        self.assertEqual(hw._held_keys, set())


class TestDesktopHwSourceSelection(unittest.TestCase):
    """DesktopHw's video/audio source selection now goes through the
    generic MultiAVRobotHardware.select_video_source/select_audio_input/
    select_audio_output (and _on_select_source for the SelectAVSource
    wire handler), not a desktop-specific binary toggle -- these are
    already covered in general by tests/test_hardware_system.py, so this
    class just confirms DesktopHw wires into that machinery correctly
    (right device strings for its own sources) rather than re-testing
    the general mechanism's own logic.
    """

    def _make_fake(self, active_video='desktop', webcam_id='webcam:/dev/video0'):
        from robonet.endpoint.hardware_system import MultiAVRobotHardware
        fake = MagicMock()
        fake._video_sources = {'desktop': '/dev/video42'}
        if webcam_id:
            fake._video_sources[webcam_id] = '/dev/video0'
        fake._active_video = active_video
        fake.root = None
        fake.select_video_source = MultiAVRobotHardware.select_video_source.__get__(fake)
        return fake

    def test_switch_to_webcam_when_available(self):
        fake = self._make_fake(active_video='desktop', webcam_id='webcam:/dev/video0')

        fake.select_video_source('webcam:/dev/video0')

        fake._gst_sender.set_source_device.assert_called_once_with('/dev/video0')
        self.assertEqual(fake._active_video, 'webcam:/dev/video0')

    def test_switch_to_unknown_id_raises_and_does_not_change_state(self):
        fake = self._make_fake(active_video='desktop', webcam_id=None)

        with self.assertRaises(KeyError):
            fake.select_video_source('webcam:/dev/video0')  # not in _video_sources

        fake._gst_sender.set_source_device.assert_not_called()
        self.assertEqual(fake._active_video, 'desktop')  # unchanged

    def test_switch_back_to_desktop(self):
        fake = self._make_fake(active_video='webcam:/dev/video0', webcam_id='webcam:/dev/video0')

        fake.select_video_source('desktop')

        fake._gst_sender.set_source_device.assert_called_once_with('/dev/video42')
        self.assertEqual(fake._active_video, 'desktop')

    def test_switch_to_same_source_is_a_noop(self):
        fake = self._make_fake(active_video='desktop')

        fake.select_video_source('desktop')

        fake._gst_sender.set_source_device.assert_not_called()



class TestFeederBusMessageHandling(unittest.TestCase):
    """_on_bus_message for both feeders -- logs errors/warnings, ignores
    everything else, never raises regardless of message type."""

    def _make_message(self, msg_type, parsed=('boom', 'debug info')):
        msg = MagicMock()
        msg.type = msg_type
        msg.parse_error.return_value = parsed
        msg.parse_warning.return_value = parsed
        return msg

    def test_video_feeder_logs_error_message(self):
        feeder = dc.DesktopVideoFeeder('/dev/video42')
        msg = self._make_message(dc.Gst.MessageType.ERROR)
        with patch('robonet.endpoint.desktop_capture.log') as mock_log:
            feeder._on_bus_message(MagicMock(), msg)
        mock_log.error.assert_called_once()

    def test_video_feeder_logs_warning_message(self):
        feeder = dc.DesktopVideoFeeder('/dev/video42')
        msg = self._make_message(dc.Gst.MessageType.WARNING)
        with patch('robonet.endpoint.desktop_capture.log') as mock_log:
            feeder._on_bus_message(MagicMock(), msg)
        mock_log.warning.assert_called_once()

    def test_video_feeder_ignores_other_message_types(self):
        feeder = dc.DesktopVideoFeeder('/dev/video42')
        msg = self._make_message(dc.Gst.MessageType.EOS)
        with patch('robonet.endpoint.desktop_capture.log') as mock_log:
            feeder._on_bus_message(MagicMock(), msg)
        mock_log.error.assert_not_called()
        mock_log.warning.assert_not_called()

    def test_audio_feeder_logs_error_message(self):
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')
        msg = self._make_message(dc.Gst.MessageType.ERROR)
        with patch('robonet.endpoint.desktop_capture.log') as mock_log:
            feeder._on_bus_message(MagicMock(), msg)
        mock_log.error.assert_called_once()

    def test_audio_feeder_logs_warning_message(self):
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')
        msg = self._make_message(dc.Gst.MessageType.WARNING)
        with patch('robonet.endpoint.desktop_capture.log') as mock_log:
            feeder._on_bus_message(MagicMock(), msg)
        mock_log.warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
