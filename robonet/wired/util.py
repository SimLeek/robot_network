"""
robonet/wired/util.py

Ethernet interface detection and static-IP setup for wired networking.
"""

from __future__ import annotations

import glob
import os
import subprocess
from typing import List, Optional

_VIRTUAL_IFACE_PREFIXES = ('docker', 'veth', 'br-', 'virbr', 'tun', 'tap', 'wg')


def find_ethernet_interfaces() -> List[str]:
    """Returns a list of valid ethernet interfaces"""

    # Ethernet-ish interfaces: has a MAC 'address' file, is not loopback,
    # does not have a 'wireless' subdirectory, and is backed by real
    # hardware (a 'device' symlink) -- this last check is what actually
    # excludes bridges/bonds regardless of name (a name-prefix list
    # can't cover every possible bridge name someone's picked; 'aibr0'
    # doesn't match any of _VIRTUAL_IFACE_PREFIXES but has no 'device').
    ifaces = []
    for path in sorted(glob.glob('/sys/class/net/*')):
        name = os.path.basename(path)
        if name == 'lo':
            continue
        if os.path.isdir(os.path.join(path, 'wireless')):
            continue
        if name.startswith(_VIRTUAL_IFACE_PREFIXES):
            continue
        if not os.path.exists(os.path.join(path, 'address')):
            continue
        if not os.path.exists(os.path.join(path, 'device')):
            continue
        ifaces.append(name)
    return ifaces


def find_connected_ethernet_interface() -> Optional[str]:
    """Returns the first connected ethernet device"""

    # First ethernet-ish interface with a cable actually plugged in
    # (carrier=1), or None. Prefer this over find_ethernet_interfaces()[0]
    # when picking one automatically since we can't connect through an
    # un-wired wired connection
    for name in find_ethernet_interfaces():
        carrier_path = f'/sys/class/net/{name}/carrier'
        try:
            with open(carrier_path, encoding='utf-8') as f:
                if f.read().strip() == '1':
                    return name
        except OSError:
            # Unreadable if the interface is administratively down; skip it.
            continue
    return None


def get_slave_type(iface: str) -> Optional[str]:
    """Returns 'bridge'/'bond'/'team' if iface is currently a slave/port
    of one, else None."""
    try:
        result = subprocess.run(
            f"nmcli -t -f GENERAL.CONNECTION device show {iface}", shell=True,
            check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return None
    con_name = result.stdout.strip().split(':', 1)[-1]
    if not con_name or con_name == '--':
        return None
    try:
        result = subprocess.run(
            f"nmcli -t -f connection.slave-type con show {con_name}", shell=True,
            check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return None
    slave_type = result.stdout.strip().split(':', 1)[-1]
    return slave_type or None


def set_wired_static(iface: str, ip: str, prefix: int = 24,
                     con_name: str = 'robonet_wired'):
    """Bring up iface with a fixed static IPv4 address via nmcli."""
    slave_type = get_slave_type(iface)
    if slave_type:
        raise RuntimeError(
            f"{iface} is currently a {slave_type} port and can't take a direct "
            f"static-IP connection profile this way. Run "
            f"examples/setup_eth_server.py (or setup_eth_client.py on an "
            f"endpoint) for guidance, or pick a different interface."
        )

    try:
        result = subprocess.run(
            f"nmcli -t -f connection.id con show {con_name}", shell=True,
            check=True, capture_output=True, text=True)
        exists = bool(result.stdout)
    except subprocess.CalledProcessError:
        exists = False  # nmcli returns non-zero when the connection doesn't exist

    try:
        commands = [f"nmcli con delete {con_name}"] if exists else []
        commands.extend([
            f"nmcli con add type ethernet ifname {iface} con-name {con_name} "
            f"autoconnect no ipv4.addresses {ip}/{prefix} ipv4.method manual ipv6.method ignore",
            f"nmcli con up {con_name}",
        ])
        for command in commands:
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Setting wired static IP failed: {e.stderr}")


def teardown_wired_static(con_name: str = 'robonet_wired'):
    """Tear down and remove the connection profile set_wired_static() created."""
    try:
        subprocess.run(f"nmcli con down {con_name}", shell=True,
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[wired] could not bring down {con_name}: {e.stderr}")
    try:
        subprocess.run(f"nmcli con delete {con_name}", shell=True,
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[wired] could not delete {con_name}: {e.stderr}")


def connect_wired(iface: Optional[str] = None, ip: Optional[str] = None,
                  prefix: Optional[int] = None, con_name: str = 'robonet_wired') -> str:
    """Endpoint-side convenience wrapper: assign this machine's ethernet
    interface a static IP in the shared wired subnet, via
    set_wired_static() above, defaulting to the endpoint-side settings
    (robonet/endpoint/settings.py's wired_endpoint_ip/wired_subnet).

    iface: which interface to configure. Auto-detects the first one with
        a cable plugged in if not given.
    ip/prefix: defaults to settings['wired_endpoint_ip']/the prefix from
        settings['wired_subnet'] -- override if you've changed those to
        something other than the shared default on both sides.

    Returns the interface name used. Raises RuntimeError if no connected
    ethernet interface was found, or if nmcli itself fails.
    """
    import robonet.endpoint.settings as settings_
    settings = settings_.get()

    if iface is None:
        iface = find_connected_ethernet_interface()
        if iface is None:
            raise RuntimeError(
                'No ethernet interface with a cable plugged in was found. '
                'Check the physical connection and try again.'
            )
    if ip is None:
        ip = settings['wired_endpoint_ip']
    if prefix is None:
        prefix = int(settings['wired_subnet'].split('/')[1])
    set_wired_static(iface, ip, prefix, con_name=con_name)
    return iface


def disconnect_wired(con_name: str = 'robonet_wired'):
    """Undo connect_wired() -- tear down the connection profile it created."""
    teardown_wired_static(con_name=con_name)
