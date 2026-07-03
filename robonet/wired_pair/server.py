"""
robonet/wired_pair/server.py

Ethernet interface detection and static-IP setup for wired pairing, used
by RadioSubSystem.NetMode.WIRED (robonet/brain/radio_system.py).

There's no server/client role split the way adhoc_pair has one (a wifi
hotspot's AP side and joining station side genuinely do different
things) -- both ends of a direct cable just need a static IP in the same
/24. So there's a single set_wired_static() here that both
robonet/brain/radio_system.py (brain side, via this module) and
robonet/wired_pair/client.py (endpoint side) call with their own
respective addresses, rather than two separate implementations.
"""

from __future__ import annotations

import glob
import os
import subprocess
from typing import List, Optional

_VIRTUAL_IFACE_PREFIXES = ('docker', 'veth', 'br-', 'virbr', 'tun', 'tap', 'wg')


def find_ethernet_interfaces() -> List[str]:
    """Ethernet-ish interfaces: has a MAC 'address' file, is not loopback,
    and does not have a 'wireless' subdirectory (the standard way to tell
    wifi apart from ethernet without needing the `iw` tool)."""
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
        ifaces.append(name)
    return ifaces


def find_connected_ethernet_interface() -> Optional[str]:
    """First ethernet-ish interface with a cable actually plugged in
    (carrier=1), or None. Prefer this over find_ethernet_interfaces()[0]
    when picking one automatically -- an unplugged NIC will never get a
    peer no matter what IP you put on it."""
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


def set_wired_static(iface: str, ip: str, prefix: int = 24,
                     con_name: str = 'robonet_wired'):
    """Bring up iface with a fixed static IPv4 address via nmcli. Mirrors
    adhoc_pair.server.set_hotspot's approach (delete-if-exists, add,
    modify, up) but for a plain wired link -- no SSID or wifi mode."""
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
    """Tear down and remove the connection profile set_wired_static()
    created. Best-effort -- logs rather than raises, since teardown
    happens during mode switches / shutdown where we'd rather not throw."""
    try:
        subprocess.run(f"nmcli con down {con_name}", shell=True,
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[wired_pair] could not bring down {con_name}: {e.stderr}")
    try:
        subprocess.run(f"nmcli con delete {con_name}", shell=True,
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"[wired_pair] could not delete {con_name}: {e.stderr}")
