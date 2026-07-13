"""
examples/setup_eth_client.py

Standalone one-time setup script: run this on an endpoint machine after
plugging in a direct ethernet cable, so its interface gets the static IP
the brain side expects.

Note: RobotRadio calls the imported functions automatically on every startup.
This is for manual setup, and would need to be re-run after a reboot.
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
