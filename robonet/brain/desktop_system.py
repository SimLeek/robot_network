"""
robonet/brain/desktop_system.py

Forwards a human's raw keyboard and mouse input to the connected endpoint as
KeyEvent/MouseEvent bursts.
"""

from __future__ import annotations

import typing
from typing import Dict, Optional

from robonet.brain.util.system_base import SubSystem
from robonet.brain.util.viewport import Viewport
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

        # Zoom/pan: desktop-specific (large or multiple monitors) --
        # doesn't belong on DisplaySubSystem, which is generic across
        # endpoint types. Robots have a movable camera instead of a
        # fixed viewport to zoom/pan around.
        self.viewport = Viewport()
        self._edit_mouse_pos: Optional[tuple] = None

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
        af_edit = self._root.displayer.af_edit
        af_edit.bind_mouse_move(self._on_edit_mouse_move)
        af_edit.bind_mouse_scroll(self._on_edit_scroll)
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
        af_edit = self._root.displayer.af_edit
        af_edit.unbind_mouse_move()
        af_edit.unbind_mouse_scroll()
        self._bound = False

    def _on_keyboard(self, key, action, modifiers):
        d = self._root.displayer
        if d is None or self._root.menu.visible:
            return  # don't send control while the menu is open
        wkeys = d.displayer.displayer.config.wnd.keys
        if action not in (wkeys.ACTION_PRESS, wkeys.ACTION_RELEASE):
            return  # ignore repeats -- the endpoint's keyDown already covers "held"
        name = keycode_to_pyautogui(key, wkeys)
        if name is None:
            return
        mods = []
        if getattr(modifiers, 'ctrl', False):  mods.append('ctrl')
        if getattr(modifiers, 'shift', False): mods.append('shift')
        if getattr(modifiers, 'alt', False):   mods.append('alt')
        self._root.radio.burst(KeyEvent(
            key=name, pressed=(action == wkeys.ACTION_PRESS), modifiers=','.join(mods)))

    def _frac_to_pixel(self, tx: float, ty: float) -> tuple:
        """Raw normalized fractions (within the full displayed canvas,
        including any letterbox padding) -> real screen pixel
        coordinates. displayarray's tx/ty are swapped relative to true
        horizontal/vertical -- swapped here, at the very last step.
        Routes through the viewport's inverse mapping first: the
        canvas position only equals the source-frame position at
        baseline zoom with matching aspect ratios and no pan, which
        isn't the normal case now that aspect ratio is preserved via
        letterboxing and zoom/pan exist. Only the lower bound (0) gets
        clamped here -- MouseEvent.x/y pack as uint32, so a negative
        value crashes at pack time. The upper bound isn't enforced
        here: pyautogui/the OS already clamps movement at the real
        screen edge on its own, using its own live screen size, rather
        than needing a second, brain-side copy of that same limit to
        stay perfectly in sync."""
        x_frac, y_frac = ty, tx
        d = self._root.displayer
        m = self._root.menu
        if d is not None and m is not None and getattr(m, 'last_img', None) is not None:
            source_h, source_w = m.last_img.shape[:2]
            display_w, display_h = d.out_res
            x_frac, y_frac = self.viewport.inverse_map(
                x_frac, y_frac, source_w, source_h, display_w, display_h)
        x = max(0, int(x_frac * self._screen_width))
        y = max(0, int(y_frac * self._screen_height))
        return x, y

    def _on_mouse_move(self, tx: float, ty: float):
        if self._root.displayer is None or self._root.menu.visible:
            return
        x, y = self._frac_to_pixel(tx, ty)
        self._root.radio.burst(MouseEvent(event_type=0, x=x, y=y, button=0, delta=0))

    def _on_mouse_press(self, tx: float, ty: float, button: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        x, y = self._frac_to_pixel(tx, ty)
        self._root.radio.burst(MouseEvent(event_type=1, x=x, y=y, button=int(button), delta=0))

    def _on_mouse_release(self, tx: float, ty: float, button: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        x, y = self._frac_to_pixel(tx, ty)
        self._root.radio.burst(MouseEvent(event_type=2, x=x, y=y, button=int(button), delta=0))

    def _on_mouse_scroll(self, y_offset: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=3, x=0, y=0, button=0, delta=int(y_offset)))

    # -- edit mode: zoom/pan the local view, doesn't touch the endpoint --

    def _on_edit_mouse_move(self, tx: float, ty: float):
        # Same swap as pass-through mode's mouse handling -- displayarray's
        # tx/ty are swapped relative to true horizontal/vertical.
        self._edit_mouse_pos = (ty, tx)

    def _on_edit_scroll(self, y_offset: float):
        d = self._root.displayer
        m = self._root.menu
        if d is None or m is None or getattr(m, 'last_img', None) is None:
            return
        source_h, source_w = m.last_img.shape[:2]
        factor = 1.1 if y_offset > 0 else (1 / 1.1 if y_offset < 0 else 1.0)
        if factor != 1.0:
            self.viewport.zoom_by(factor, source_w, source_h, d.out_res[0], d.out_res[1])

    def check_edge_pan(self, source_w: int, source_h: int, display_w: int, display_h: int,
                      frame_time: float):
        """While in edit mode, pans when the mouse sits within 10% of
        an edge of the display window. Called every frame (not just on
        mouse-move) by DisplaySubSystem's render loop, so it keeps
        panning while the mouse holds still near an edge, matching how
        edge-scroll works in most editors."""
        if self._edit_mouse_pos is None:
            return
        tx, ty = self._edit_mouse_pos
        edge = 0.10
        pan_speed = 0.5 * frame_time  # fraction of the source frame per second
        dx = dy = 0.0
        if tx < edge:
            dx = -pan_speed * (edge - tx) / edge
        elif tx > 1.0 - edge:
            dx = pan_speed * (tx - (1.0 - edge)) / edge
        if ty < edge:
            dy = -pan_speed * (edge - ty) / edge
        elif ty > 1.0 - edge:
            dy = pan_speed * (ty - (1.0 - edge)) / edge
        if dx or dy:
            self.viewport.pan_by(dx, dy, source_w, source_h, display_w, display_h)

    def async_loops(self, sm: 'ServerSystem'):
        return []  # purely event-driven -- no polling loop needed
