"""
examples/setup_eth_server.py

Brain-side counterpart to setup_eth_client.py: one-time static IP setup
for a direct ethernet link. Run after plugging in the cable.

    python -m examples.setup_eth_server
"""

from __future__ import annotations

from robonet.wired.util import set_wired_static, find_connected_ethernet_interface
import robonet.brain.settings as settings_

settings = settings_.get()


def setup_eth_server(iface=None, ip=None, prefix=None, con_name='robonet_wired'):
    if iface is None:
        iface = find_connected_ethernet_interface()
        if iface is None:
            raise RuntimeError('No ethernet interface with a cable plugged in was found.')
    if ip is None:
        ip = settings['wired_our_ip']
    if prefix is None:
        prefix = int(settings['wired_subnet'].split('/')[1])
    set_wired_static(iface, ip, prefix, con_name=con_name)
    return iface


if __name__ == '__main__':
    used_iface = setup_eth_server()
    print(f"[setup_eth_server] configured {used_iface} with a static IP in "
         f"{settings['wired_subnet']}.")
