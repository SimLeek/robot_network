"""
tests/test_key_watchdog.py

Lossy networks drop KeyEvents -- including releases -- which used to
leave the endpoint mashing a key forever (observed live: F11 repeating
until process kill). The brain re-sends key-down for every held key
every KEY_REFRESH_INTERVAL_S; the endpoint auto-releases any held key
not refreshed within KEY_WATCHDOG_TIMEOUT_S.
"""

import unittest
from unittest.mock import MagicMock, patch


def _make_brain_sub():
    from robonet.brain.desktop_system import DesktopSubSystem
    endpoint = MagicMock()
    endpoint.streams = [{'name': 'screen', 'type': 'video', 'width': 1920, 'height': 1080}]
    sub = DesktopSubSystem(endpoint=endpoint)
    sub._root = MagicMock()
    sub._root.menu.visible = False
    sub._root.displayer = None
    return sub


class TestBrainRefreshLoop(unittest.TestCase):

    def test_held_key_gets_refreshed(self):
        sub = _make_brain_sub()
        sub.set_input_source('ai')
        sub.ai_key_press('f11')
        sub._root.radio.burst.reset_mock()

        sub._refresh_held_keys()

        sub._root.radio.burst.assert_called_once()
        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.key, sent.pressed), ('f11', True))

    def test_release_stops_the_refreshing(self):
        sub = _make_brain_sub()
        sub.set_input_source('ai')
        sub.ai_key_press('f11')
        sub.ai_key_release('f11')
        sub._root.radio.burst.reset_mock()

        sub._refresh_held_keys()

        sub._root.radio.burst.assert_not_called()

    def test_release_untracks_even_when_transmit_is_gated(self):
        # The exact stuck-key scenario: the release itself never gets
        # transmitted (input source switched mid-hold), but the brain
        # must STOP refreshing so the endpoint's watchdog can fire.
        sub = _make_brain_sub()
        sub.set_input_source('ai')
        sub.ai_key_press('f11')
        sub.set_input_source('human')     # switch mid-hold
        sub.ai_key_release('f11')         # transmit gated -- but still untracked
        sub._root.radio.burst.reset_mock()

        sub._refresh_held_keys()

        sub._root.radio.burst.assert_not_called()

    def test_refresh_preserves_modifiers(self):
        sub = _make_brain_sub()
        sub.set_input_source('ai')
        sub.ai_key_press('a', modifiers='ctrl,shift')
        sub._root.radio.burst.reset_mock()

        sub._refresh_held_keys()

        self.assertEqual(sub._root.radio.burst.call_args[0][0].modifiers, 'ctrl,shift')

    def test_multiple_held_keys_all_refresh(self):
        sub = _make_brain_sub()
        sub.set_input_source('ai')
        sub.ai_key_press('w')
        sub.ai_key_press('shift')
        sub._root.radio.burst.reset_mock()

        sub._refresh_held_keys()

        keys = sorted(c.args[0].key for c in sub._root.radio.burst.call_args_list)
        self.assertEqual(keys, ['shift', 'w'])


def _make_endpoint_hw():
    from robonet.endpoint import desktop_hardware as dh
    fake_settings = {'camera_device': None, 'mic_device': None, 'speaker_device': None}
    with patch.object(dh, 'pyautogui') as mock_pg, \
         patch.object(dh, 'settings', fake_settings), \
         patch.object(dh, 'find_camera_devices', return_value=[]), \
         patch.object(dh, 'get_first_mic_device', return_value='hw:0,0'), \
         patch.object(dh, 'get_first_speaker_device', return_value='hw:0,0'):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        mock_pg.FailSafeException = type('FailSafeException', (Exception,), {})
        hw = dh.DesktopHw()
    return hw, mock_pg, dh


class TestEndpointWatchdog(unittest.TestCase):

    def _key_event(self, key='f11', pressed=True):
        return MagicMock(key=key, pressed=pressed)

    def test_stale_held_key_is_auto_released(self):
        from robonet.endpoint.desktop_hardware import KEY_WATCHDOG_TIMEOUT_S
        hw, mock_pg, dh = _make_endpoint_hw()
        hw._held_keys['f11'] = 100.0  # last refreshed at t=100

        with patch.object(dh, 'pyautogui', mock_pg):
            hw._key_watchdog_sweep(100.0 + KEY_WATCHDOG_TIMEOUT_S + 0.1)

        mock_pg.keyUp.assert_called_once_with('f11')
        self.assertNotIn('f11', hw._held_keys)

    def test_fresh_held_key_is_left_alone(self):
        hw, mock_pg, dh = _make_endpoint_hw()
        hw._held_keys['f11'] = 100.0

        with patch.object(dh, 'pyautogui', mock_pg):
            hw._key_watchdog_sweep(100.5)  # within the timeout

        mock_pg.keyUp.assert_not_called()
        self.assertIn('f11', hw._held_keys)

    def test_refresh_updates_the_stamp_without_a_second_keydown(self):
        hw, mock_pg, dh = _make_endpoint_hw()
        with patch.object(dh, 'pyautogui', mock_pg), \
             patch.object(dh.time, 'monotonic', side_effect=[100.0, 100.4]):
            hw._on_key_event('brain', self._key_event(pressed=True))   # real press
            hw._on_key_event('brain', self._key_event(pressed=True))   # watchdog refresh

        mock_pg.keyDown.assert_called_once()          # not pressed twice
        self.assertEqual(hw._held_keys['f11'], 100.4)  # but the stamp advanced

    def test_release_still_works_normally(self):
        hw, mock_pg, dh = _make_endpoint_hw()
        with patch.object(dh, 'pyautogui', mock_pg):
            hw._on_key_event('brain', self._key_event(pressed=True))
            hw._on_key_event('brain', self._key_event(pressed=False))

        mock_pg.keyUp.assert_called_once_with('f11')
        self.assertNotIn('f11', hw._held_keys)


if __name__ == '__main__':
    unittest.main()
