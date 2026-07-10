"""
examples/desktop/desktop_endpoint.py -- A remote desktop endpoint

Start once and leave running in the background
(e.g. as a systemd --user service);

One-time setup before first use (loads v4l2loopback and snd-aloop):
    sudo ./examples/setup_desktop_capture.sh

If connecting over a direct wired (ethernet) link,
also run this once on the endpoint side to get a static IP:
    python -m examples.setup_eth_client
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from robonet.endpoint.base import RobotNode
from robonet.endpoint.desktop_capture import DesktopCaptureError
from robonet.endpoint.desktop_hardware import DesktopHw
from robonet.endpoint.radio_system import RobotRadio

log = logging.getLogger(__name__)

CAPTURE_WIDTH  = -1
CAPTURE_HEIGHT = -1
CAPTURE_FPS    = 15


if __name__ == '__main__':
    radio = RobotRadio(endpoint_type='desktop')
    try:
        hw = DesktopHw(capture_width=CAPTURE_WIDTH, capture_height=CAPTURE_HEIGHT,
                      capture_fps=CAPTURE_FPS)
    except DesktopCaptureError as e:
        print(f'\n[desktop_endpoint] {e}\n', file=sys.stderr)
        sys.exit(1)

    main_node = RobotNode(radio, hw)
    asyncio.run(main_node.run())
