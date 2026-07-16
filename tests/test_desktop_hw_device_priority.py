"""
tests/test_desktop_hw_device_priority.py

Regression tests: DesktopHw used to unconditionally call
find_camera_devices()/get_first_speaker_device(), ignoring any
explicit configuration entirely -- MultiAVRobotHardware supports
constructor args for this, but DesktopHw never passed anything
through. Now prefers an explicit constructor arg, then the
settings.py override, then auto-detect only if neither is given.
"""

import unittest
from unittest.mock import patch, MagicMock


class TestDesktopHwDevicePriority(unittest.TestCase):

    def _make_settings(self, camera_device=None, mic_device=None, speaker_device=None):
        return {'camera_device': camera_device, 'mic_device': mic_device, 'speaker_device': speaker_device}

    def test_explicit_camera_arg_wins_over_everything(self):
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings(camera_device='/dev/video9')), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=['/dev/video0']), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value='hw:0,0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            hw = DesktopHw(camera='/dev/video5')

        self.assertIn('webcam:/dev/video5', hw._video_sources)

    def test_settings_camera_used_when_no_explicit_arg(self):
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings(camera_device='/dev/video9')), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=['/dev/video0']), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value='hw:0,0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            hw = DesktopHw()

        self.assertIn('webcam:/dev/video9', hw._video_sources)
        self.assertNotIn('webcam:/dev/video0', hw._video_sources)

    def test_auto_detect_used_when_neither_arg_nor_setting_given(self):
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings()), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=['/dev/video0']), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value='hw:0,0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            hw = DesktopHw()

        self.assertIn('webcam:/dev/video0', hw._video_sources)

    def test_explicit_speaker_arg_wins_over_settings_and_auto_detect(self):
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings(speaker_device='hw:9,9')), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=[]), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value='hw:0,0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            hw = DesktopHw(speaker='hw:5,5')

        self.assertIn('speaker:hw:5,5', hw._audio_outputs)

    def test_settings_speaker_used_when_no_explicit_arg(self):
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings(speaker_device='hw:9,9')), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=[]), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value='hw:0,0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            hw = DesktopHw()

        self.assertIn('speaker:hw:9,9', hw._audio_outputs)
        self.assertNotIn('speaker:hw:0,0', hw._audio_outputs)

    def test_default_constructor_still_works_with_no_args(self):
        # The exact regression risk: existing callers like
        # examples/desktop/desktop_endpoint.py call DesktopHw() with no
        # arguments at all -- must keep working unchanged by default.
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings()), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=[]), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value='hw:0,0'), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            hw = DesktopHw()  # must not raise

        self.assertIsNotNone(hw)


if __name__ == '__main__':
    unittest.main()


class TestDesktopHwMicPriority(unittest.TestCase):
    """Mic follows the same priority as camera/speaker: constructor
    arg, then the mic_device setting, then auto-detect. The desktop mix
    always stays first (and therefore the default active input)."""

    def _make_settings(self, camera_device=None, mic_device=None, speaker_device=None):
        return {'camera_device': camera_device, 'mic_device': mic_device, 'speaker_device': speaker_device}

    def _build(self, ctor_mic=None, setting_mic=None, detected_mic='hw:0,0'):
        from robonet.endpoint.desktop_hardware import DesktopHw
        with patch('robonet.endpoint.desktop_hardware.pyautogui') as mock_pg, \
             patch('robonet.endpoint.desktop_hardware.settings', self._make_settings(mic_device=setting_mic)), \
             patch('robonet.endpoint.desktop_hardware.find_camera_devices', return_value=[]), \
             patch('robonet.endpoint.desktop_hardware.get_first_mic_device', return_value=detected_mic), \
             patch('robonet.endpoint.desktop_hardware.get_first_speaker_device', return_value='hw:0,0'):
            mock_pg.size.return_value = MagicMock(width=1920, height=1080)
            return DesktopHw(mic=ctor_mic)

    def test_explicit_mic_arg_wins(self):
        hw = self._build(ctor_mic='hw:5,5', setting_mic='hw:9,9')
        self.assertIn('mic:hw:5,5', hw._audio_inputs)
        self.assertNotIn('mic:hw:9,9', hw._audio_inputs)

    def test_settings_mic_used_when_no_arg(self):
        hw = self._build(setting_mic='hw:9,9')
        self.assertIn('mic:hw:9,9', hw._audio_inputs)

    def test_auto_detect_when_neither_given(self):
        hw = self._build(detected_mic='hw:2,0')
        self.assertIn('mic:hw:2,0', hw._audio_inputs)

    def test_desktop_mix_stays_first_and_default(self):
        hw = self._build(setting_mic='hw:9,9')
        first_key = next(iter(hw._audio_inputs))
        self.assertNotIn('mic:', first_key)  # desktop mix entry remains the default active input
