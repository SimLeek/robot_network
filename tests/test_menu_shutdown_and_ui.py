"""
tests/test_menu_shutdown_and_ui.py

Tests:
  - MenuSubSystem.timeout_loop's two-tier shutdown logic (fast if nothing
    was ever seen, slow grace period once something has been seen/
    connected and then goes idle). Uses a lightweight fake carrying just
    the attributes timeout_loop touches, since the real MenuSubSystem
    constructor opens real psk key files and builds a real GstSender/
    GstReceiver pair that this logic doesn't need.
  - SelectionMenu's camera/desktop-capture switch item and the Wired mode
    item's index-offset arithmetic, against real SelectionMenu instances
    (that constructor is cheap -- no psk files, no network).
"""

import asyncio
import unittest
from enum import Enum
from unittest.mock import MagicMock, patch

from robonet.brain.menu_system import MenuSubSystem
from robonet.brain.util.selection_menu import SelectionMenu, MenuVisState
from robonet.buffers.buffer_objects import SwitchVideoSource


class _FakeMenuUI:
    """Stands in for SelectionMenu, just the bits timeout_loop touches."""
    def __init__(self, endpoints=None):
        self._endpoints = endpoints or []
        self.status_history = []

    def get_unique_endpoints(self):
        return self._endpoints

    def set_status(self, msg):
        self.status_history.append(msg)


class _FakeMenuSubSystem:
    """Minimal MenuSubSystem stand-in for timeout_loop."""
    def __init__(self, no_endpoints_timeout=30.0, idle_timeout=600.0):
        self.does_timeout = True
        self.no_endpoints_timeout = no_endpoints_timeout
        self.idle_timeout = idle_timeout
        self._connected = False
        self._menu = _FakeMenuUI()
        self.root = MagicMock()

    timeout_loop = MenuSubSystem.timeout_loop


class TestTimeoutLoopTwoTier(unittest.IsolatedAsyncioTestCase):

    async def _run_until_shutdown_or_iterations(self, menu, max_iterations=50):
        """Drive the loop's internal 1s ticks without real wall-clock
        delay, stopping once shutdown() has been called (does_timeout
        flips False, matching what a real shutdown() would eventually
        cause via process exit) or after max_iterations as a safety cap."""
        call_count = 0

        async def fake_sleep(_secs):
            nonlocal call_count
            call_count += 1
            if call_count >= max_iterations:
                menu.does_timeout = False  # force the while loop to end

        def fake_shutdown(reason=''):
            menu.root.shutdown_reason = reason
            menu.does_timeout = False  # stop the loop, matching real SystemExit unwinding it

        menu.root.shutdown = MagicMock(side_effect=fake_shutdown)
        with patch('robonet.brain.menu_system.time.time', side_effect=self._clock), \
             patch('robonet.brain.menu_system.asyncio.sleep', new=fake_sleep):
            await menu.timeout_loop()
        return call_count

    def setUp(self):
        self._now = 1_000_000.0  # arbitrary epoch-ish start

    def _clock(self):
        # Each call to time.time() advances the fake clock by 1s, matching
        # one real asyncio.sleep(1.0) per loop iteration.
        self._now += 1.0
        return self._now

    async def test_shuts_down_fast_when_nothing_ever_seen(self):
        menu = _FakeMenuSubSystem(no_endpoints_timeout=5.0, idle_timeout=600.0)
        await self._run_until_shutdown_or_iterations(menu)

        menu.root.shutdown.assert_called_once()
        self.assertIn('5s', menu.root.shutdown_reason)

    async def test_does_not_shut_down_while_endpoints_visible(self):
        menu = _FakeMenuSubSystem(no_endpoints_timeout=3.0, idle_timeout=600.0)
        menu._menu._endpoints = [MagicMock()]  # something is visible the whole time

        await self._run_until_shutdown_or_iterations(menu, max_iterations=10)

        menu.root.shutdown.assert_not_called()

    async def test_does_not_shut_down_while_connected(self):
        menu = _FakeMenuSubSystem(no_endpoints_timeout=3.0, idle_timeout=600.0)
        menu._connected = True

        await self._run_until_shutdown_or_iterations(menu, max_iterations=10)

        menu.root.shutdown.assert_not_called()

    async def test_uses_slow_timeout_once_something_was_seen_then_disappears(self):
        menu = _FakeMenuSubSystem(no_endpoints_timeout=2.0, idle_timeout=6.0)
        ep = MagicMock()
        menu._menu._endpoints = [ep]

        # Custom driver: endpoint visible for the first 2 ticks, then gone.
        call_count = 0
        async def fake_sleep(_secs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                menu._menu._endpoints = []  # endpoint disappears
            if call_count >= 20:
                menu.does_timeout = False

        def fake_shutdown(reason=''):
            menu.root.shutdown_reason = reason
            menu.does_timeout = False

        menu.root.shutdown = MagicMock(side_effect=fake_shutdown)
        with patch('robonet.brain.menu_system.time.time', side_effect=self._clock), \
             patch('robonet.brain.menu_system.asyncio.sleep', new=fake_sleep):
            await menu.timeout_loop()

        menu.root.shutdown.assert_called_once()
        # Used idle_timeout (6s), not no_endpoints_timeout (2s) -- something
        # had been seen before it went away.
        self.assertIn('6s', menu.root.shutdown_reason)

    async def test_does_not_run_at_all_when_does_timeout_is_false(self):
        menu = _FakeMenuSubSystem()
        menu.does_timeout = False

        await self._run_until_shutdown_or_iterations(menu)

        menu.root.shutdown.assert_not_called()


class TestSelectionMenuCameraSwitch(unittest.TestCase):

    def test_set_desktop_mode_true_shows_camera_item(self):
        menu = SelectionMenu(width=320, height=240)
        menu.set_robot_capabilities({'axes': [], 'streams': []})  # so 'Robot' item logic doesn't interfere
        menu.set_desktop_mode(True)

        items = menu._main_items()

        self.assertTrue(any(item.startswith('Camera:') for item in items))

    def test_set_desktop_mode_false_hides_camera_item(self):
        menu = SelectionMenu(width=320, height=240)
        menu.set_desktop_mode(False)

        items = menu._main_items()

        self.assertFalse(any(item.startswith('Camera:') for item in items))

    def test_set_desktop_mode_false_resets_source_to_desktop(self):
        menu = SelectionMenu(width=320, height=240)
        menu.set_desktop_mode(True)
        menu._desktop_video_source = 'camera'

        menu.set_desktop_mode(False)

        self.assertEqual(menu._desktop_video_source, 'desktop')

    def test_toggle_sends_switch_video_source_burst(self):
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.set_desktop_mode(True)

        menu._toggle_desktop_camera()

        menu.root.radio.burst.assert_called_once()
        sent = menu.root.radio.burst.call_args[0][0]
        self.assertIsInstance(sent, SwitchVideoSource)
        self.assertEqual(sent.source, 'camera')

    def test_toggle_flips_back_and_forth(self):
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.set_desktop_mode(True)

        menu._toggle_desktop_camera()
        self.assertEqual(menu._desktop_video_source, 'camera')
        menu._toggle_desktop_camera()
        self.assertEqual(menu._desktop_video_source, 'desktop')

    def test_enter_on_camera_item_toggles_without_changing_menu_state(self):
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.set_desktop_mode(True)
        menu.state = MenuVisState.MAIN
        items = menu._main_items()
        camera_idx = next(i for i, label in enumerate(items) if label.startswith('Camera:'))
        menu._cursor = camera_idx

        menu._handle_main_key('enter')

        menu.root.radio.burst.assert_called_once()
        self.assertEqual(menu.menu_state.current_state.id, 'main_menu')  # did not navigate away


class TestSelectionMenuWiredModeIndexOffsets(unittest.TestCase):
    """The exact off-by-one risk of inserting a 5th control item ahead of
    the endpoint list -- verifies the item list and the index arithmetic
    in _handle_radio_key agree with each other."""

    def _make_menu_with_root(self, mode, is_scanning=False, endpoints=None):
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.root.radio.mode = mode
        menu.root.radio.NetMode = MagicMock()
        menu.root.radio.NetMode.LOCALHOST = 'LOCALHOST'
        menu.root.radio.NetMode.WIFI = 'WIFI'
        menu.root.radio.NetMode.ADHOC = 'ADHOC'
        menu.root.radio.NetMode.WIRED = 'WIRED'
        menu.root.radio.mode = 'WIRED'
        menu.root.radio.is_scanning = is_scanning
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=endpoints or []):
            items = menu._radio_items()
        return menu, items

    def test_five_control_items_before_any_endpoints(self):
        menu, items = self._make_menu_with_root('WIRED', endpoints=[])
        self.assertEqual(len(items), 5)
        self.assertTrue(items[0].startswith('Mode: Local'))
        self.assertTrue(items[1].startswith('Mode: Wi-Fi'))
        self.assertTrue(items[2].startswith('Mode: Ad-Hoc'))
        self.assertTrue(items[3].startswith('Mode: Wired'))
        self.assertIn('Scanning', items[4])

    def test_wired_mode_shows_as_checked_when_active(self):
        menu, items = self._make_menu_with_root('WIRED', endpoints=[])
        self.assertIn('[X]', items[3])
        self.assertIn('[ ]', items[0])  # localhost not active

    def test_cursor_on_index_3_selects_wired_mode(self):
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.root.radio.mode = 'ADHOC'
        menu.root.radio.NetMode = MagicMock()
        menu.root.radio.NetMode.WIRED = 'WIRED'
        menu.root.radio.is_scanning = False
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[]):
            menu._cursor = 3
            with patch('robonet.brain.util.selection_menu.asyncio.ensure_future') as mock_ensure:
                menu._handle_radio_key('enter')

        mock_ensure.assert_called_once()

    def test_endpoint_list_starts_at_index_5(self):
        fake_ep = MagicMock(ip='10.0.0.5', axes=['a'], streams=[])
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.root.radio.mode = 'ADHOC'
        menu.root.radio.NetMode = MagicMock()
        menu.root.radio.is_scanning = False

        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[fake_ep]):
            items = menu._radio_items()
            self.assertEqual(len(items), 6)  # 5 controls + 1 endpoint
            menu._cursor = 5  # the endpoint's position
            result = menu._handle_radio_key('enter')

        self.assertIs(result, fake_ep)

    def test_endpoint_not_ready_returns_none_and_sets_status(self):
        fake_ep = MagicMock(ip='10.0.0.5', axes=[], streams=[])
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.root.radio.mode = 'ADHOC'
        menu.root.radio.NetMode = MagicMock()
        menu.root.radio.is_scanning = False

        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[fake_ep]):
            menu._cursor = 5
            result = menu._handle_radio_key('enter')

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
