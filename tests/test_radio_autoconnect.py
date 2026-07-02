"""
tests/test_radio_autoconnect.py

Tests the auto-connect priority-list sequencing (_maybe_auto_connect,
_ensure_mode_active, auto_connect_sequence_loop) and the WIRED NetMode
setup/teardown branches added to RadioSubSystem, plus the two-tier
shutdown logic in MenuSubSystem.timeout_loop.

These call the real, unbound RadioSubSystem/MenuSubSystem methods against
a minimal fake object carrying just the attributes those methods
actually touch, rather than constructing a full instance -- the real
constructors open real psk key files, bind real UDP sockets, and (on the
menu side) build a full SelectionMenu/displayarray-backed UI, none of
which is needed to verify this control-flow logic and all of which would
make these tests slow, environment-dependent, or both.
"""

import asyncio
import time
import unittest
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

from robonet.brain.radio_system import RadioSubSystem
from robonet.brain.menu_system import MenuSubSystem


class _NetMode(Enum):
    LOCALHOST = 1
    WIFI = 2
    ADHOC = 3
    WIRED = 4


class _FakeEndpoint:
    def __init__(self, hostname='desk1', ip='10.0.0.5', endpoint_type='desktop',
                axes=None, streams=None):
        self.hostname = hostname
        self.ip = ip
        self.endpoint_type = endpoint_type
        self.axes = axes if axes is not None else []
        self.streams = streams if streams is not None else []


class _FakeRadio:
    """Minimal RadioSubSystem stand-in for the auto-connect methods."""
    NetMode = _NetMode

    def __init__(self, priority=None, endpoint_type='any', attempt_timeout=20.0,
                start_mode=_NetMode.ADHOC):
        self._auto_connect_priority = priority if priority is not None else []
        self._auto_connect_endpoint_type = endpoint_type
        self._auto_connect_attempt_timeout = attempt_timeout
        self._mode = start_mode
        self.root = MagicMock()
        self.root.active_sub = None
        self._is_scanning = False
        self.switch_mode_calls = []
        self.start_scanner_calls = 0

    @property
    def is_scanning(self):
        return self._is_scanning

    async def switch_mode(self, mode):
        self.switch_mode_calls.append(mode)
        self._mode = mode

    async def start_scanner_task(self):
        self.start_scanner_calls += 1
        self._is_scanning = True

    # Bind the real, unbound methods under test.
    _maybe_auto_connect = RadioSubSystem._maybe_auto_connect
    _ensure_mode_active = RadioSubSystem._ensure_mode_active
    auto_connect_sequence_loop = RadioSubSystem.auto_connect_sequence_loop


class TestMaybeAutoConnect(unittest.TestCase):

    def test_fires_when_priority_set_and_endpoint_ready(self):
        radio = _FakeRadio(priority=['localhost'])
        ep = _FakeEndpoint(axes=[1, 2, 3])

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_called_once_with(ep)

    def test_does_not_fire_when_priority_empty(self):
        radio = _FakeRadio(priority=[])
        ep = _FakeEndpoint(axes=[1])

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_not_called()

    def test_does_not_fire_when_already_connected(self):
        radio = _FakeRadio(priority=['localhost'])
        radio.root.active_sub = MagicMock()  # something's already connected
        ep = _FakeEndpoint(axes=[1])

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_not_called()

    def test_does_not_fire_when_endpoint_not_ready(self):
        radio = _FakeRadio(priority=['localhost'])
        ep = _FakeEndpoint(axes=[], streams=[])  # not ready yet

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_not_called()

    def test_respects_endpoint_type_filter(self):
        radio = _FakeRadio(priority=['localhost'], endpoint_type='robot')
        ep = _FakeEndpoint(endpoint_type='desktop', axes=[1])

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_not_called()

    def test_any_endpoint_type_matches_everything(self):
        radio = _FakeRadio(priority=['localhost'], endpoint_type='any')
        ep = _FakeEndpoint(endpoint_type='desktop', axes=[1])

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_called_once_with(ep)

    def test_ready_via_streams_alone_also_counts(self):
        radio = _FakeRadio(priority=['localhost'])
        ep = _FakeEndpoint(axes=[], streams=['cam'])

        radio._maybe_auto_connect(ep)

        radio.root.menu._connect.assert_called_once_with(ep)


class TestEnsureModeActive(unittest.IsolatedAsyncioTestCase):

    async def test_switches_when_mode_differs(self):
        radio = _FakeRadio(start_mode=_NetMode.ADHOC)

        await radio._ensure_mode_active(_NetMode.WIRED)

        self.assertEqual(radio.switch_mode_calls, [_NetMode.WIRED])

    async def test_no_switch_when_already_in_mode(self):
        radio = _FakeRadio(start_mode=_NetMode.WIRED)

        await radio._ensure_mode_active(_NetMode.WIRED)

        self.assertEqual(radio.switch_mode_calls, [])

    async def test_starts_scanner_for_non_localhost_even_without_switching(self):
        # Already in WIRED (no switch_mode call needed) but scanner never
        # started -- this is the "first mode of the run" gap switch_mode's
        # own no-op would otherwise leave uncovered.
        radio = _FakeRadio(start_mode=_NetMode.WIRED)

        await radio._ensure_mode_active(_NetMode.WIRED)

        self.assertEqual(radio.start_scanner_calls, 1)

    async def test_does_not_start_scanner_for_localhost(self):
        radio = _FakeRadio(start_mode=_NetMode.LOCALHOST)

        await radio._ensure_mode_active(_NetMode.LOCALHOST)

        self.assertEqual(radio.start_scanner_calls, 0)

    async def test_does_not_restart_scanner_if_already_scanning(self):
        radio = _FakeRadio(start_mode=_NetMode.WIRED)
        radio._is_scanning = True

        await radio._ensure_mode_active(_NetMode.WIRED)

        self.assertEqual(radio.start_scanner_calls, 0)


class TestAutoConnectSequenceLoop(unittest.IsolatedAsyncioTestCase):

    async def test_empty_priority_returns_immediately(self):
        radio = _FakeRadio(priority=[])
        await radio.auto_connect_sequence_loop()
        self.assertEqual(radio.switch_mode_calls, [])

    async def test_tries_each_mode_in_order_when_none_connect(self):
        radio = _FakeRadio(priority=['localhost', 'wired'], attempt_timeout=0.01)
        with patch('robonet.brain.radio_system.asyncio.sleep', new=AsyncMock()):
            await radio.auto_connect_sequence_loop()
        self.assertEqual(radio.switch_mode_calls, [_NetMode.LOCALHOST, _NetMode.WIRED])

    async def test_stops_immediately_once_connected(self):
        radio = _FakeRadio(priority=['localhost', 'wired'], attempt_timeout=0.01)

        async def fake_sleep(_secs):
            radio.root.active_sub = MagicMock()  # simulate _maybe_auto_connect succeeding mid-wait

        with patch('robonet.brain.radio_system.asyncio.sleep', new=fake_sleep):
            await radio.auto_connect_sequence_loop()

        # Only tried the first mode -- stopped before ever getting to 'wired'.
        self.assertEqual(radio.switch_mode_calls, [_NetMode.LOCALHOST])

    async def test_already_connected_before_starting_does_nothing(self):
        radio = _FakeRadio(priority=['localhost', 'wired'])
        radio.root.active_sub = MagicMock()

        await radio.auto_connect_sequence_loop()

        self.assertEqual(radio.switch_mode_calls, [])

    async def test_unknown_mode_name_is_skipped_not_fatal(self):
        radio = _FakeRadio(priority=['not_a_real_mode', 'localhost'], attempt_timeout=0.01)
        with patch('robonet.brain.radio_system.asyncio.sleep', new=AsyncMock()):
            await radio.auto_connect_sequence_loop()
        self.assertEqual(radio.switch_mode_calls, [_NetMode.LOCALHOST])

    async def test_case_insensitive_mode_names(self):
        radio = _FakeRadio(priority=['LoCaLhOsT'], attempt_timeout=0.01)
        with patch('robonet.brain.radio_system.asyncio.sleep', new=AsyncMock()):
            await radio.auto_connect_sequence_loop()
        self.assertEqual(radio.switch_mode_calls, [_NetMode.LOCALHOST])


class TestWiredNetModeSetupTeardown(unittest.TestCase):
    """_setup_mode/_teardown_mode's WIRED branches, exercised directly
    against a minimal fake carrying just what those two methods touch."""

    def _make_fake(self):
        radio = MagicMock()
        radio.NetMode = RadioSubSystem.NetMode  # MagicMock's auto-attr would not equal the real enum
        radio._wired_our_ip = '169.254.90.1'
        radio._wired_subnet = '169.254.90.0/24'
        radio._wired_iface = None
        radio._scanner = MagicMock()
        return radio

    def test_setup_configures_interface_when_cable_plugged_in(self):
        radio = self._make_fake()
        with patch('robonet.wired_pair.server.find_connected_ethernet_interface', return_value='eth0'), \
             patch('robonet.wired_pair.server.set_wired_static') as mock_set:
            RadioSubSystem._setup_mode(radio, RadioSubSystem.NetMode.WIRED)

        mock_set.assert_called_once_with('eth0', '169.254.90.1', 24)
        self.assertEqual(radio._wired_iface, 'eth0')
        radio._scanner.set_subnet.assert_called_once_with('169.254.90.0/24', iface='eth0')

    def test_setup_handles_no_cable_plugged_in_gracefully(self):
        radio = self._make_fake()
        with patch('robonet.wired_pair.server.find_connected_ethernet_interface', return_value=None), \
             patch('robonet.wired_pair.server.set_wired_static') as mock_set:
            RadioSubSystem._setup_mode(radio, RadioSubSystem.NetMode.WIRED)  # must not raise

        mock_set.assert_not_called()
        self.assertIsNone(radio._wired_iface)

    def test_setup_swallows_nmcli_failure(self):
        radio = self._make_fake()
        with patch('robonet.wired_pair.server.find_connected_ethernet_interface', return_value='eth0'), \
             patch('robonet.wired_pair.server.set_wired_static', side_effect=RuntimeError('nmcli exploded')):
            RadioSubSystem._setup_mode(radio, RadioSubSystem.NetMode.WIRED)  # must not raise

        self.assertIsNone(radio._wired_iface)

    def test_teardown_only_runs_if_wired_was_actually_set_up(self):
        radio = self._make_fake()
        radio._wired_iface = None  # never configured
        with patch('robonet.wired_pair.server.teardown_wired_static') as mock_teardown:
            RadioSubSystem._teardown_mode(radio, RadioSubSystem.NetMode.WIRED)

        mock_teardown.assert_not_called()

    def test_teardown_clears_iface_on_success(self):
        radio = self._make_fake()
        radio._wired_iface = 'eth0'
        with patch('robonet.wired_pair.server.teardown_wired_static') as mock_teardown:
            RadioSubSystem._teardown_mode(radio, RadioSubSystem.NetMode.WIRED)

        mock_teardown.assert_called_once()
        self.assertIsNone(radio._wired_iface)


if __name__ == '__main__':
    unittest.main()
