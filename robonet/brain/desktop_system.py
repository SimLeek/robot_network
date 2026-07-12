"""
robonet/brain/desktop_system.py

Forwards a human's raw keyboard and mouse input to the connected endpoint as
KeyEvent/MouseEvent bursts.
"""

from __future__ import annotations

import typing
from typing import Dict, Optional

from robonet.brain.util.system_base import SubSystem
from robonet.buffers.buffer_objects import KeyEvent, MouseEvent
from robonet.logging_setup import setup_logging

log = setup_logging()

if typing.TYPE_CHECKING:
    from robonet.brain.util.network_scanner import Endpoint
    from robonet.brain.main_system import ServerSystem

from robonet.desktop_control_spec import (
    keycode_to_pyautogui, DESKTOP_CONTROL_INTERFACE_SPEC, describe_control_interface,
)

class DesktopSubSystem(SubSystem):
    """Server-side desktop counterpart."""
    _endpoint_type = 'desktop'

    def __init__(self, endpoint: 'Endpoint'):
        self._endpoint = endpoint
        self._root: Optional['ServerSystem'] = None
        self._running = False
        self._bound = False
        self._tasks = []
        self.handlers = {}

        # Mouse positions arrive as normalized 0-1 texel coordinates
        # (fraction across the captured frame) -- scaling them by the
        # endpoint's actual screen resolution (reported in its
        # capabilities, not the possibly-downscaled transmitted size)
        # is what turns them into real pixel coordinates on that
        # machine. Defaults to 1920x1080 if the endpoint didn't report
        # a 'screen' stream for some reason, rather than dividing by
        # zero or refusing to move the mouse at all.
        self._screen_width, self._screen_height = 1920, 1080
        for s in getattr(endpoint, 'streams', None) or []:
            if s.get('name') == 'screen' and s.get('type') == 'video':
                self._screen_width = s.get('width', self._screen_width)
                self._screen_height = s.get('height', self._screen_height)
                break

    def setup(self, root: 'ServerSystem'):
        self._root = root

    def start(self):
        self._running = True
        self._bind_input()
        log.info(describe_control_interface())

    def stop(self):
        self._running = False
        self._unbind_input()

    # -- input forwarding ----------------------------------------------

    def _bind_input(self):
        if self._root.displayer is None:
            return  # AI-direct-only-driven session: no viewer exists to display to
        af = self._root.displayer.af_thru
        af.bind_keyboard(self._on_keyboard)
        af.bind_mouse_move(self._on_mouse_move)
        af.bind_mouse_press(self._on_mouse_press)
        af.bind_mouse_release(self._on_mouse_release)
        af.bind_mouse_scroll(self._on_mouse_scroll)
        self._bound = True

    def _unbind_input(self):
        if not self._bound or self._root is None or self._root.displayer is None:
            return
        af = self._root.displayer.af_thru
        af.unbind_keyboard()
        af.unbind_mouse_move()
        af.unbind_mouse_press()
        af.unbind_mouse_release()
        af.unbind_mouse_scroll()
        self._bound = False

    def _on_keyboard(self, key, action, modifiers):
        d = self._root.displayer
        if d is None or self._root.menu.visible:
            return  # don't send control while the menu is open
        wkeys = d.displayer.displayer.config.wnd.keys
        if action not in (wkeys.ACTION_PRESS, wkeys.ACTION_RELEASE):
            return  # ignore repeats -- the endpoint's keyDown already covers "held"
        name = keycode_to_pyautogui(key)
        if name is None:
            return
        mods = []
        if getattr(modifiers, 'ctrl', False):  mods.append('ctrl')
        if getattr(modifiers, 'shift', False): mods.append('shift')
        if getattr(modifiers, 'alt', False):   mods.append('alt')
        self._root.radio.burst(KeyEvent(
            key=name, pressed=(action == wkeys.ACTION_PRESS), modifiers=','.join(mods)))

    def _on_mouse_move(self, tx: float, ty: float):
        if self._root.displayer is None or self._root.menu.visible:
            return
        x = int(tx * self._screen_width)
        y = int(ty * self._screen_height)
        self._root.radio.burst(MouseEvent(event_type=0, x=x, y=y, button=0, delta=0))

    def _on_mouse_press(self, x: float, y: float, button: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=1, x=int(x), y=int(y), button=int(button), delta=0))

    def _on_mouse_release(self, x: float, y: float, button: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=2, x=int(x), y=int(y), button=int(button), delta=0))

    def _on_mouse_scroll(self, y_offset: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=3, x=0, y=0, button=0, delta=int(y_offset)))

    def async_loops(self, sm: 'ServerSystem'):
        return []  # purely event-driven -- no polling loop needed
