"""
tests/test_wired_pair.py

Tests robonet/wired_pair/server.py: ethernet interface detection against
a mocked /sys/class/net filesystem, and the exact nmcli commands
set_wired_static/teardown_wired_static issue, against a mocked
subprocess.run -- no real network interfaces or nmcli needed.
"""

import subprocess
import unittest
from unittest.mock import patch, mock_open, MagicMock, call

from robonet.wired_pair import server as wp


class TestFindEthernetInterfaces(unittest.TestCase):

    def _mock_fs(self, ifaces_with_wireless, ifaces_with_address):
        """ifaces_with_wireless: names that should look like wifi (have a
        'wireless' subdir). ifaces_with_address: names that have a MAC
        'address' file (real ethernet interfaces always do)."""
        def fake_isdir(path):
            return path.endswith('/wireless') and any(
                path == f'/sys/class/net/{n}/wireless' for n in ifaces_with_wireless)

        def fake_exists(path):
            return path.endswith('/address') and any(
                path == f'/sys/class/net/{n}/address' for n in ifaces_with_address)

        return fake_isdir, fake_exists

    @patch('robonet.wired_pair.server.os.path.exists')
    @patch('robonet.wired_pair.server.os.path.isdir')
    @patch('robonet.wired_pair.server.glob.glob')
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

    @patch('robonet.wired_pair.server.os.path.exists', return_value=True)
    @patch('robonet.wired_pair.server.os.path.isdir', return_value=False)
    @patch('robonet.wired_pair.server.glob.glob')
    def test_multiple_real_interfaces_all_returned_sorted(self, mock_glob, _isdir, _exists):
        mock_glob.return_value = ['/sys/class/net/eth1', '/sys/class/net/eth0']

        result = wp.find_ethernet_interfaces()

        self.assertEqual(result, ['eth0', 'eth1'])  # sorted by glob's own sort in the source

    @patch('robonet.wired_pair.server.os.path.exists', return_value=False)
    @patch('robonet.wired_pair.server.os.path.isdir', return_value=False)
    @patch('robonet.wired_pair.server.glob.glob', return_value=[])
    def test_no_interfaces_returns_empty_list(self, *_mocks):
        self.assertEqual(wp.find_ethernet_interfaces(), [])


class TestFindConnectedEthernetInterface(unittest.TestCase):

    @patch('robonet.wired_pair.server.find_ethernet_interfaces')
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

    @patch('robonet.wired_pair.server.find_ethernet_interfaces')
    def test_returns_none_when_nothing_plugged_in(self, mock_find):
        mock_find.return_value = ['eth0', 'eth1']
        with patch('builtins.open', mock_open(read_data='0\n')):
            self.assertIsNone(wp.find_connected_ethernet_interface())

    @patch('robonet.wired_pair.server.find_ethernet_interfaces')
    def test_unreadable_carrier_is_skipped_not_fatal(self, mock_find):
        mock_find.return_value = ['eth0', 'eth1']

        def fake_open(path, *a, **kw):
            if 'eth0' in path:
                raise OSError('administratively down')
            return mock_open(read_data='1\n').return_value

        with patch('builtins.open', side_effect=fake_open):
            result = wp.find_connected_ethernet_interface()

        self.assertEqual(result, 'eth1')

    @patch('robonet.wired_pair.server.find_ethernet_interfaces', return_value=[])
    def test_no_interfaces_at_all_returns_none(self, _find):
        self.assertIsNone(wp.find_connected_ethernet_interface())


class TestSetWiredStatic(unittest.TestCase):

    @patch('robonet.wired_pair.server.subprocess.run')
    def test_new_connection_skips_delete_and_adds_then_brings_up(self, mock_run):
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

    @patch('robonet.wired_pair.server.subprocess.run')
    def test_existing_connection_is_deleted_first(self, mock_run):
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

    @patch('robonet.wired_pair.server.subprocess.run')
    def test_custom_con_name_used_throughout(self, mock_run):
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

    @patch('robonet.wired_pair.server.subprocess.run')
    def test_nmcli_failure_raises_runtime_error_with_stderr(self, mock_run):
        def side_effect(cmd, **kwargs):
            if 'con show' in cmd:
                return MagicMock(stdout='')
            raise subprocess.CalledProcessError(1, cmd, stderr='nmcli: device eth0 not found')

        mock_run.side_effect = side_effect

        with self.assertRaises(RuntimeError) as ctx:
            wp.set_wired_static('eth0', '169.254.90.1')

        self.assertIn('device eth0 not found', str(ctx.exception))


class TestTeardownWiredStatic(unittest.TestCase):

    @patch('robonet.wired_pair.server.subprocess.run')
    def test_brings_down_then_deletes(self, mock_run):
        mock_run.return_value = MagicMock(stdout='ok')

        wp.teardown_wired_static()

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn('con down robonet_wired', commands[0])
        self.assertIn('con delete robonet_wired', commands[1])

    @patch('robonet.wired_pair.server.subprocess.run')
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

    @patch('robonet.wired_pair.server.subprocess.run')
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

    @patch('robonet.wired_pair.server.subprocess.run')
    def test_custom_con_name_used(self, mock_run):
        mock_run.return_value = MagicMock(stdout='ok')

        wp.teardown_wired_static(con_name='my_link')

        commands = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn('con down my_link', commands[0])
        self.assertIn('con delete my_link', commands[1])


if __name__ == '__main__':
    unittest.main()
