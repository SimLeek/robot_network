"""
tests/test_desktop_capture.py

Tests DesktopHw construction/capabilities (direct ximagesrc/pulsesrc
capture, no v4l2loopback/ALSA-loopback -- see
tests/test_streamer_desktop_sources.py for the GstSender pipeline side
of that), GstSender.set_source_device/set_mic_device (device
switching), and DesktopHw's generic AV-source selection wiring.
"""

import os
import shutil
import tempfile
import unittest
from collections import namedtuple
from unittest.mock import patch, mock_open, MagicMock

import numpy as np


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

    def _mock_pyautogui(self, screen_width=1920, screen_height=1080):
        mock_pag = MagicMock()
        Size = namedtuple('Size', 'width height')
        mock_pag.size.return_value = Size(screen_width, screen_height)
        return mock_pag

    def _construct(self, webcam=None, speaker='hw:0,0', **kwargs):
        with patch('robonet.endpoint.desktop_hardware.pyautogui', self._mock_pyautogui()), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices',
                  return_value=[webcam] if webcam else []), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device',
                  return_value=speaker):
            from robonet.endpoint.desktop_hardware import DesktopHw
            return DesktopHw(**kwargs)

    def test_construction_uses_ximagesrc_directly(self):
        from robonet.gst_io.streamer_unencrypted import VIDEO_SOURCE_XIMAGESRC
        hw = self._construct()
        self.assertEqual(hw._video_sources['desktop'], VIDEO_SOURCE_XIMAGESRC)
        self.assertEqual(hw._active_video, 'desktop')

    def test_construction_uses_desktop_audio_mix_directly(self):
        from robonet.gst_io.streamer_unencrypted import AUDIO_SOURCE_DESKTOP_MIX
        hw = self._construct()
        self.assertEqual(hw._audio_inputs['desktop-audio'], AUDIO_SOURCE_DESKTOP_MIX)

    def test_construction_raises_clear_error_when_pyautogui_unavailable(self):
        with patch('robonet.endpoint.desktop_hardware.pyautogui', None), \
             patch('robonet.endpoint.desktop_hardware._PYAUTOGUI_IMPORT_ERROR', KeyError('DISPLAY')):
            from robonet.endpoint.desktop_hardware import DesktopHw, DesktopCaptureError
            with self.assertRaises(DesktopCaptureError) as ctx:
                DesktopHw()
            self.assertIn('DISPLAY', str(ctx.exception))

    def test_pyautogui_unavailable_error_path_does_not_itself_crash(self):
        """Regression test: _PYAUTOGUI_IMPORT_ERROR previously had no
        default value, so if pyautogui was None for any reason other
        than the module's own except block having just run, building
        the error message raised NameError instead of the intended
        DesktopCaptureError. Only patches pyautogui itself, not the
        error variable, to catch that gap if it comes back."""
        with patch('robonet.endpoint.desktop_hardware.pyautogui', None):
            from robonet.endpoint.desktop_hardware import DesktopHw, DesktopCaptureError
            try:
                DesktopHw()
                self.fail('expected DesktopCaptureError')
            except DesktopCaptureError:
                pass
            except NameError as e:
                self.fail(f'_PYAUTOGUI_IMPORT_ERROR regression: {e}')

    def test_real_webcam_detected_when_present(self):
        hw = self._construct(webcam='/dev/video0')
        self.assertIn('webcam:/dev/video0', hw._video_sources)
        self.assertEqual(hw._video_sources['webcam:/dev/video0'], '/dev/video0')

    def test_no_webcam_found_degrades_gracefully(self):
        hw = self._construct(webcam=None)
        self.assertEqual(list(hw._video_sources), ['desktop'])

    def test_no_speaker_found_degrades_gracefully(self):
        from robonet.gst_io.devices import DeviceNotFoundError
        with patch('robonet.endpoint.desktop_hardware.pyautogui', self._mock_pyautogui()), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=[]), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device',
                  side_effect=DeviceNotFoundError('no speaker')):
            from robonet.endpoint.desktop_hardware import DesktopHw
            hw = DesktopHw()  # must not raise
        self.assertEqual(hw._audio_outputs, {})

    def test_build_capabilities_matches_module_function(self):
        hw = self._construct()
        with patch('robonet.endpoint.desktop_hardware.pyautogui', self._mock_pyautogui()):
            caps = hw.build_capabilities()
        self.assertEqual(caps.endpoint_type, 'desktop')
        self.assertGreater(len(caps.axes()), 0)  # keys/mouse are real axes, not a statue
        self.assertGreater(len(caps.streams()), 0)  # video/audio are real streams

    def test_capabilities_axes_match_control_interface_spec(self):
        from robonet.desktop_control_spec import DESKTOP_CONTROL_INTERFACE_SPEC
        hw = self._construct()
        with patch('robonet.endpoint.desktop_hardware.pyautogui', self._mock_pyautogui()):
            axes = hw.build_capabilities().axes()
        self.assertEqual(len(axes), len(DESKTOP_CONTROL_INTERFACE_SPEC))
        self.assertEqual({a['name'] for a in axes}, set(DESKTOP_CONTROL_INTERFACE_SPEC))

    def test_capabilities_streams_report_actual_screen_resolution(self):
        hw = self._construct()
        with patch('robonet.endpoint.desktop_hardware.pyautogui',
                  self._mock_pyautogui(screen_width=2560, screen_height=1440)):
            streams = hw.build_capabilities().streams()
        types = {s['type'] for s in streams}
        self.assertEqual(types, {'video', 'audio'})
        screen = next(s for s in streams if s['name'] == 'screen')
        self.assertEqual((screen['width'], screen['height']), (2560, 1440))

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

    def test_setup_logs_active_video_source(self):
        hw = self._construct()
        with self.assertLogs('robonet.endpoint.desktop_hardware', level='INFO') as cm:
            hw.setup(MagicMock())
        self.assertTrue(any('desktop' in line for line in cm.output))

    def test_stop_releases_held_input(self):
        hw = self._construct()
        hw._held_keys = {'a'}
        hw._held_buttons = {'left'}
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pag:
            hw.stop()
            mock_pag.keyUp.assert_called_once_with('a')
            mock_pag.mouseUp.assert_called_once_with(button='left')
        self.assertEqual(hw._held_keys, set())
        self.assertEqual(hw._held_buttons, set())

    def test_halt_releases_held_input(self):
        hw = self._construct()
        hw._held_keys = {'shift'}
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pag:
            hw.halt()
            mock_pag.keyUp.assert_called_once_with('shift')

    def test_release_all_held_swallows_pyautogui_errors(self):
        hw = self._construct()
        hw._held_keys = {'a', 'b'}
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pag:
            mock_pag.keyUp.side_effect = Exception('X server gone')
            hw._release_all_held()  # must not raise
        self.assertEqual(hw._held_keys, set())


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





if __name__ == '__main__':
    unittest.main()
