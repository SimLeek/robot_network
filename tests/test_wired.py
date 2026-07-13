"""
tests/test_wired.py

Tests robonet/wired/util.py: ethernet interface detection against
a mocked /sys/class/net filesystem, and the exact nmcli commands
set_wired_static/teardown_wired_static issue, against a mocked
subprocess.run -- no real network interfaces or nmcli needed.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, mock_open, MagicMock, call

from robonet.wired import util as wp


class TestFindEthernetInterfaces(unittest.TestCase):

    def _mock_fs(self, ifaces_with_wireless, ifaces_with_address, ifaces_with_device=None):
        """ifaces_with_wireless: names that should look like wifi (have a
        'wireless' subdir). ifaces_with_address: names that have a MAC
        'address' file (real ethernet interfaces always do).
        ifaces_with_device: names backed by real hardware (a 'device'
        symlink) -- defaults to the same set as ifaces_with_address."""
        if ifaces_with_device is None:
            ifaces_with_device = ifaces_with_address

        def fake_isdir(path):
            return path.endswith('/wireless') and any(
                path == f'/sys/class/net/{n}/wireless' for n in ifaces_with_wireless)

        def fake_exists(path):
            if path.endswith('/address'):
                return any(path == f'/sys/class/net/{n}/address' for n in ifaces_with_address)
            if path.endswith('/device'):
                return any(path == f'/sys/class/net/{n}/device' for n in ifaces_with_device)
            return False

        return fake_isdir, fake_exists

    @patch('robonet.wired.util.os.path.exists')
    @patch('robonet.wired.util.os.path.isdir')
    @patch('robonet.wired.util.glob.glob')
    def test_filters_loopback_wireless_virtual_and_addressless(self, mock_glob, mock_isdir, mock_exists):
        mock_glob.return_value = [
            '/sys/class/net/lo',
            '/sys/class/net/wlan0',
            '/sys/class/net/docker0',
            '/sys/class/net/veth1234',
            '/sys/class/net/eth0',
            '/sys/class/net/eth1',   # no address file -- e.g. not really present
        ]
        mock_isdir.side_effect, exists_fn = self._mock_fs(
            ifaces_with_wireless=['wlan0'], ifaces_with_address=['eth0', 'wlan0', 'docker0', 'veth1234'])
        mock_exists.side_effect = exists_fn

        result = wp.find_ethernet_interfaces()

        self.assertEqual(result, ['eth0'])

    @patch('robonet.wired.util.os.path.exists')
    @patch('robonet.wired.util.os.path.isdir', return_value=False)
    @patch('robonet.wired.util.glob.glob')
    def test_excludes_arbitrarily_named_bridge_lacking_a_real_device(self, mock_glob, _isdir, mock_exists):
        # The actual bug this is a regression test for: a bridge named
        # 'aibr0' doesn't match any name-prefix heuristic (_VIRTUAL_IFACE_
        # PREFIXES only knows 'br-', not every possible bridge name), but
        # it's not backed by real hardware -- no 'device' symlink -- and
        # that's true regardless of what it's named.
        mock_glob.return_value = ['/sys/class/net/aibr0', '/sys/class/net/enp0s31f6']
        _, exists_fn = self._mock_fs(
            ifaces_with_wireless=[], ifaces_with_address=['aibr0', 'enp0s31f6'],
            ifaces_with_device=['enp0s31f6'])  # aibr0 has an address but no device -- it's the bridge itself
        mock_exists.side_effect = exists_fn

        result = wp.find_ethernet_interfaces()

        self.assertEqual(result, ['enp0s31f6'])

    @patch('robonet.wired.util.os.path.exists', return_value=True)
    @patch('robonet.wired.util.os.path.isdir', return_value=False)
    @patch('robonet.wired.util.glob.glob')
    def test_multiple_real_interfaces_all_returned_sorted(self, mock_glob, _isdir, _exists):
        mock_glob.return_value = ['/sys/class/net/eth1', '/sys/class/net/eth0']

        result = wp.find_ethernet_interfaces()

        self.assertEqual(result, ['eth0', 'eth1'])  # sorted by glob's own sort in the source

    @patch('robonet.wired.util.os.path.exists', return_value=False)
    @patch('robonet.wired.util.os.path.isdir', return_value=False)
    @patch('robonet.wired.util.glob.glob', return_value=[])
    def test_no_interfaces_returns_empty_list(self, *_mocks):
        self.assertEqual(wp.find_ethernet_interfaces(), [])


class TestFindConnectedEthernetInterface(unittest.TestCase):

    @patch('robonet.wired.util.find_ethernet_interfaces')
    def test_returns_first_with_carrier(self, mock_find):
        mock_find.return_value = ['eth0', 'eth1']
        carrier_content = {'eth0': '0\n', 'eth1': '1\n'}

        def fake_open(path, *a, **kw):
            for name, content in carrier_content.items():
                if path == f'/sys/class/net/{name}/carrier':
                    return mock_open(read_data=content).return_value
            raise OSError('not found')

        with patch('builtins.open', side_effect=fake_open):
            result = wp.find_connected_ethernet_interface()

        self.assertEqual(result, 'eth1')

    @patch('robonet.wired.util.find_ethernet_interfaces')
    def test_returns_none_when_nothing_plugged_in(self, mock_find):
        mock_find.return_value = ['eth0', 'eth1']
        with patch('builtins.open', mock_open(read_data='0\n')):
            self.assertIsNone(wp.find_connected_ethernet_interface())

    @patch('robonet.wired.util.find_ethernet_interfaces')
    def test_unreadable_carrier_is_skipped_not_fatal(self, mock_find):
        mock_find.return_value = ['eth0', 'eth1']

        def fake_open(path, *a, **kw):
            if 'eth0' in path:
                raise OSError('administratively down')
            return mock_open(read_data='1\n').return_value

        with patch('builtins.open', side_effect=fake_open):
            result = wp.find_connected_ethernet_interface()

        self.assertEqual(result, 'eth1')

    @patch('robonet.wired.util.find_ethernet_interfaces', return_value=[])
    def test_no_interfaces_at_all_returns_none(self, _find):
        self.assertIsNone(wp.find_connected_ethernet_interface())


class TestGetSlaveType(unittest.TestCase):

    @patch('robonet.wired.util.subprocess.run')
    def test_returns_none_for_unenslaved_interface(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout='GENERAL.CONNECTION:eth0-plain'),
            MagicMock(stdout='connection.slave-type:'),
        ]
        self.assertIsNone(wp.get_slave_type('eth0'))

    @patch('robonet.wired.util.subprocess.run')
    def test_returns_bridge_when_enslaved(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout='GENERAL.CONNECTION:eth0-bridge-slave'),
            MagicMock(stdout='connection.slave-type:bridge'),
        ]
        self.assertEqual(wp.get_slave_type('eth0'), 'bridge')

    @patch('robonet.wired.util.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'x'))
    def test_returns_none_on_nmcli_failure(self, _run):
        self.assertIsNone(wp.get_slave_type('eth0'))

    @patch('robonet.wired.util.subprocess.run')
    def test_returns_none_when_device_has_no_active_connection(self, mock_run):
        mock_run.side_effect = [MagicMock(stdout='GENERAL.CONNECTION:--')]
        self.assertIsNone(wp.get_slave_type('eth0'))


class TestSetWiredStaticSlaveDetection(unittest.TestCase):

    @patch('robonet.wired.util.get_slave_type', return_value='bridge')
    def test_raises_clear_error_without_attempting_nmcli(self, _slave):
        with self.assertRaises(RuntimeError) as ctx:
            wp.set_wired_static('eth0', '169.254.90.1')
        self.assertIn('bridge', str(ctx.exception))
        self.assertIn('setup_eth_server', str(ctx.exception))


class TestSetWiredStatic(unittest.TestCase):

    @patch('robonet.wired.util.get_slave_type', return_value=None)
    @patch('robonet.wired.util.subprocess.run')
    def test_new_connection_skips_delete_and_adds_then_brings_up(self, mock_run, _slave):
        # First call (existence check) returns empty stdout -> doesn't exist
        mock_run.side_effect = [
            MagicMock(stdout=''),                     # nmcli con show (existence check)
            MagicMock(stdout='connection added'),      # nmcli con add
            MagicMock(stdout='connection activated'),  # nmcli con up
        ]

        wp.set_wired_static('eth0', '169.254.90.1', prefix=24)

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertIn('con show robonet_wired', commands[0])
        self.assertNotIn('delete', commands[1])
        self.assertIn('type ethernet ifname eth0', commands[1])
        self.assertIn('169.254.90.1/24', commands[1])
        self.assertIn('ipv4.method manual', commands[1])
        self.assertIn('con up robonet_wired', commands[2])

    @patch('robonet.wired.util.get_slave_type', return_value=None)
    @patch('robonet.wired.util.subprocess.run')
    def test_existing_connection_is_deleted_first(self, mock_run, _slave):
        mock_run.side_effect = [
            MagicMock(stdout='robonet_wired'),          # exists
            MagicMock(stdout='deleted'),                # delete
            MagicMock(stdout='added'),                  # add
            MagicMock(stdout='up'),                      # up
        ]

        wp.set_wired_static('eth0', '169.254.90.1')

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertIn('con delete robonet_wired', commands[1])

    @patch('robonet.wired.util.get_slave_type', return_value=None)
    @patch('robonet.wired.util.subprocess.run')
    def test_custom_con_name_used_throughout(self, mock_run, _slave):
        mock_run.side_effect = [
            MagicMock(stdout=''),
            MagicMock(stdout='added'),
            MagicMock(stdout='up'),
        ]

        wp.set_wired_static('eth0', '10.0.0.5', con_name='my_link')

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn('con show my_link', commands[0])
        self.assertIn('con-name my_link', commands[1])
        self.assertIn('con up my_link', commands[2])

    @patch('robonet.wired.util.get_slave_type', return_value=None)
    @patch('robonet.wired.util.subprocess.run')
    def test_nmcli_failure_raises_runtime_error_with_stderr(self, mock_run, _slave):
        def side_effect(cmd, **kwargs):
            if 'con show' in cmd:
                return MagicMock(stdout='')
            raise subprocess.CalledProcessError(1, cmd, stderr='nmcli: device eth0 not found')

        mock_run.side_effect = side_effect

        with self.assertRaises(RuntimeError) as ctx:
            wp.set_wired_static('eth0', '169.254.90.1')

        self.assertIn('device eth0 not found', str(ctx.exception))


class TestTeardownWiredStatic(unittest.TestCase):

    @patch('robonet.wired.util.subprocess.run')
    def test_brings_down_then_deletes(self, mock_run):
        mock_run.return_value = MagicMock(stdout='ok')

        wp.teardown_wired_static()

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn('con down robonet_wired', commands[0])
        self.assertIn('con delete robonet_wired', commands[1])

    @patch('robonet.wired.util.subprocess.run')
    def test_does_not_raise_when_down_fails(self, mock_run):
        def side_effect(cmd, **kwargs):
            if 'con down' in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr='not active')
            return MagicMock(stdout='ok')
        mock_run.side_effect = side_effect

        try:
            wp.teardown_wired_static()  # must not raise
        except Exception as e:
            self.fail(f'teardown_wired_static raised: {e}')

    @patch('robonet.wired.util.subprocess.run')
    def test_does_not_raise_when_delete_fails(self, mock_run):
        def side_effect(cmd, **kwargs):
            if 'con delete' in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr='not found')
            return MagicMock(stdout='ok')
        mock_run.side_effect = side_effect

        try:
            wp.teardown_wired_static()  # must not raise
        except Exception as e:
            self.fail(f'teardown_wired_static raised: {e}')

    @patch('robonet.wired.util.subprocess.run')
    def test_custom_con_name_used(self, mock_run):
        mock_run.return_value = MagicMock(stdout='ok')

        wp.teardown_wired_static(con_name='my_link')

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn('con down my_link', commands[0])
        self.assertIn('con delete my_link', commands[1])


class TestConnectWired(unittest.TestCase):

    @patch('robonet.wired.util.set_wired_static')
    @patch('robonet.wired.util.find_connected_ethernet_interface')
    def test_auto_detects_interface_and_uses_settings_defaults(self, mock_find, mock_set):
        mock_find.return_value = 'eth0'

        result = wp.connect_wired()

        self.assertEqual(result, 'eth0')
        mock_set.assert_called_once_with('eth0', '169.254.90.2', 24, con_name='robonet_wired')

    @patch('robonet.wired.util.set_wired_static')
    @patch('robonet.wired.util.find_connected_ethernet_interface')
    def test_explicit_iface_skips_auto_detection(self, mock_find, mock_set):
        result = wp.connect_wired(iface='eth1')

        mock_find.assert_not_called()
        self.assertEqual(result, 'eth1')
        mock_set.assert_called_once_with('eth1', '169.254.90.2', 24, con_name='robonet_wired')

    @patch('robonet.wired.util.set_wired_static')
    @patch('robonet.wired.util.find_connected_ethernet_interface')
    def test_explicit_ip_and_prefix_override_settings(self, mock_find, mock_set):
        mock_find.return_value = 'eth0'

        wp.connect_wired(ip='10.0.0.5', prefix=16)

        mock_set.assert_called_once_with('eth0', '10.0.0.5', 16, con_name='robonet_wired')

    @patch('robonet.wired.util.find_connected_ethernet_interface', return_value=None)
    def test_raises_clear_error_when_no_cable_plugged_in(self, _find):
        with self.assertRaises(RuntimeError) as ctx:
            wp.connect_wired()
        self.assertIn('cable', str(ctx.exception))

    @patch('robonet.wired.util.set_wired_static')
    @patch('robonet.wired.util.find_connected_ethernet_interface', return_value='eth0')
    def test_custom_con_name_passed_through(self, _find, mock_set):
        wp.connect_wired(con_name='my_link')
        mock_set.assert_called_once_with('eth0', '169.254.90.2', 24, con_name='my_link')


class TestDisconnectWired(unittest.TestCase):

    @patch('robonet.wired.util.teardown_wired_static')
    def test_delegates_to_teardown_wired_static(self, mock_teardown):
        wp.disconnect_wired()
        mock_teardown.assert_called_once_with(con_name='robonet_wired')

    @patch('robonet.wired.util.teardown_wired_static')
    def test_custom_con_name(self, mock_teardown):
        wp.disconnect_wired(con_name='my_link')
        mock_teardown.assert_called_once_with(con_name='my_link')


class TestRobotRadioWiredIntegration(unittest.TestCase):
    """RobotRadio.__init__'s new auto_wired_setup integration -- the
    actual endpoint-side receiving code, not just the standalone
    setup_eth_client script. This sandbox's pyzmq build lacks draft-
    socket support (zmq.DISH/RADIO need it), so socket creation is
    mocked; everything else in __init__ (including the new wired block)
    runs for real."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='robonet_endpoint_radio_test_')
        self.psk_path = os.path.join(self.tmpdir, 'psk.key')
        self.server_psk_path = os.path.join(self.tmpdir, 'server_psk.key')
        with open(self.psk_path, 'wb') as f:
            f.write(os.urandom(32))
        with open(self.server_psk_path, 'wb') as f:
            f.write(os.urandom(32))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_settings(self, **overrides):
        vals = {
            'psk_file': self.psk_path, 'server_psk_file': self.server_psk_path,
            'our_port': 0, 'their_port': 0,
            'auto_wired_setup': False,
        }
        vals.update(overrides)
        fake = MagicMock()
        fake.__getitem__.side_effect = vals.__getitem__
        return fake

    def _construct(self, **setting_overrides):
        from robonet.endpoint.radio_system import RobotRadio
        with patch('robonet.endpoint.radio_system.settings', self._make_settings(**setting_overrides)), \
             patch('robonet.endpoint.radio_system.zmq.asyncio.Context.instance') as mock_ctx_cls:
            mock_ctx = MagicMock()
            mock_ctx.socket.return_value = MagicMock()
            mock_ctx_cls.return_value = mock_ctx
            return RobotRadio()

    def test_wired_setup_not_attempted_when_disabled(self):
        with patch('robonet.wired.util.connect_wired') as mock_connect:
            self._construct(auto_wired_setup=False)
        mock_connect.assert_not_called()

    def test_wired_setup_attempted_when_enabled(self):
        with patch('robonet.wired.util.connect_wired', return_value='eth0') as mock_connect:
            self._construct(auto_wired_setup=True)
        mock_connect.assert_called_once()

    def test_wired_setup_failure_does_not_prevent_construction(self):
        # No cable plugged in is the normal case for most endpoints --
        # must not raise or block listening on other interfaces.
        with patch('robonet.wired.util.connect_wired',
                  side_effect=RuntimeError('no cable plugged in')):
            radio = self._construct(auto_wired_setup=True)  # must not raise
        self.assertIsNotNone(radio)


if __name__ == '__main__':
    unittest.main()
