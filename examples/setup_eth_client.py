"""
examples/setup_eth_client.py

Standalone one-time setup script: run this on an endpoint machine after
plugging in a direct ethernet cable, so its interface gets a static IP
in the same subnet the brain side configures itself into (see
wired_our_ip/wired_subnet in robonet/brain/settings.py, and the matching
wired_endpoint_ip/wired_subnet in robonet/endpoint/settings.py).

Why this needs to exist at all: RobotRadio's DISH socket binds to
"udp://0.0.0.0:{our_port}", which listens on every interface the machine
has -- but "every interface" still means every interface that actually
has an IP address. A direct point-to-point ethernet cable has no DHCP
server on either end, so without this, the endpoint's ethernet interface
has no address at all, and the brain's WhoAreYou probes (aimed at the
169.254.90.0/24 subnet it configured on its own end) have nothing to
reach even though RobotRadio itself is already listening correctly.

The actual logic (connect_wired/disconnect_wired) lives in
robonet.wired.util, not here -- this is just the runnable entry point
for people who'd rather run it once manually than set
auto_wired_setup=True in robonet/endpoint/settings.py and have
RobotRadio call it automatically on every startup instead.

Usage (run once after plugging in the cable; nmcli's autoconnect=no
means this needs to be re-run after a reboot, or turned into a systemd
unit if the cable stays plugged in permanently -- see
setup_permanent_endpoint.sh for that):
    python -m examples.setup_eth_client
"""

from __future__ import annotations

from robonet.wired.util import connect_wired
import robonet.endpoint.settings as settings_

settings = settings_.get()


if __name__ == '__main__':
    used_iface = connect_wired()
    print(f"[setup_eth_client] configured {used_iface} with a static IP in "
         f"{settings['wired_subnet']}. You can now start the endpoint "
         f"script, e.g.:\n"
         f"    python examples/desktop/desktop_endpoint.py")
