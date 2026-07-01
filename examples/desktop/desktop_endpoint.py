"""
examples/desktop/desktop_endpoint.py -- desktop-mode endpoint node.

The desktop analog of examples/basicpibot/robot_endpoint.py. Instead of a
MasterPi robot, this streams the machine's own screen and audio out and
accepts remote keyboard/mouse control -- basically a remote desktop.

Meant to be started once and left running in the background (e.g. as a
systemd --user service, or just in a terminal you leave open); the brain
side connects to it like any other endpoint. Toggle between the desktop
capture and a physical webcam (if present) from the brain's main menu.

One-time setup before first use:
    ./examples/desktop/setup_desktop_capture.sh
(loads the v4l2loopback and snd-aloop kernel modules -- needs sudo once).

Network mode and ports are read from ~/.robotar/settings.json, same as
robot_endpoint.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robonet.endpoint.base import RobotNode
from robonet.endpoint.desktop_capture import DesktopCaptureError
from robonet.endpoint.desktop_hardware import DesktopHw
from robonet.endpoint.radio_system import RobotRadio

log = logging.getLogger(__name__)

# Desktop capture wants to be legible, not tiny -- 1080p by default rather
# than the 640x480 the webcam-oriented cam_res setting defaults to. See
# DesktopHw.__init__ for why this can't just be a settings.json default.
CAPTURE_WIDTH  = 1920
CAPTURE_HEIGHT = 1080
CAPTURE_FPS    = 30


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
