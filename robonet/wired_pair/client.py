"""
robonet/wired_pair/client.py

Endpoint-side counterpart to server.py's set_wired_static -- run this
once on the endpoint machine (the one running robot_endpoint.py or
desktop_endpoint.py) after plugging in the direct ethernet cable, so its
interface gets a static IP in the same subnet the brain side configures
itself into (see wired_our_ip/wired_subnet in robonet/brain/settings.py,
and the matching wired_endpoint_ip/wired_subnet in
robonet/endpoint/settings.py).

Why this needs to exist at all: RobotRadio's DISH socket binds to
"udp://0.0.0.0:{our_port}", which listens on every interface the machine
has -- but "every interface" still means every interface that actually
has an IP address. A direct point-to-point ethernet cable has no DHCP
server on either end, so without this, the endpoint's ethernet interface
has no address at all, and the brain's WhoAreYou probes (aimed at the
169.254.90.0/24 subnet it configured on its own end) have nothing to
reach even though RobotRadio itself is already listening correctly.

This mirrors what adhoc_pair/client.py, local_wifi_pair/client.py, and
localhost_pair/client.py exist to do for their own transports -- worth
noting those three are standalone scripts built on an older
client_unicast_communication/client_udp_discovery pattern that predates
RobotRadio/RobotNode and isn't invoked by the current endpoint entry
points either (grepped robonet/endpoint/ and examples/ for any of the
three -- nothing references them). Rather than copy that older,
disconnected pattern, this does the one thing wired mode actually needs
(a static IP, via the same set_wired_static() the brain side already
uses) and stays a simple standalone step, same as
examples/desktop/setup_desktop_capture.sh.

Usage (run once after plugging in the cable; nmcli's autoconnect=no
means this needs to be re-run after a reboot, or turned into a systemd
unit if the cable stays plugged in permanently):
    python -m robonet.wired_pair.client
"""

from __future__ import annotations

from typing import Optional

from robonet.wired_pair.server import (
    find_connected_ethernet_interface, set_wired_static, teardown_wired_static,
)
import robonet.endpoint.settings as settings_

settings = settings_.get()


def connect_wired(iface: Optional[str] = None, ip: Optional[str] = None,
                  prefix: Optional[int] = None, con_name: str = 'robonet_wired') -> str:
    """Assign this machine's ethernet interface a static IP in the shared
    wired subnet, via the same set_wired_static() the brain side uses on
    its own end.

    iface: which interface to configure. Auto-detects the first one with
        a cable plugged in if not given.
    ip/prefix: defaults to settings['wired_endpoint_ip']/the prefix from
        settings['wired_subnet'] -- override if you've changed those to
        something other than the shared default on both sides.

    Returns the interface name used. Raises RuntimeError if no connected
    ethernet interface was found, or if nmcli itself fails.
    """
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


if __name__ == '__main__':
    used_iface = connect_wired()
    print(f"[wired_pair] configured {used_iface} with a static IP in "
         f"{settings['wired_subnet']}. You can now start the endpoint "
         f"script, e.g.:\n"
         f"    python examples/desktop/desktop_endpoint.py")
