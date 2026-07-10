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

# Best-effort GLFW keycode -> pyautogui key name mapping
# Printable ASCII keys (32-126) map directly via chr() already
_NAMED_KEY_MAP: Dict[int, str] = {
    256: 'esc', 257: 'enter', 258: 'tab', 259: 'backspace',
    260: 'insert', 261: 'delete',
    262: 'right', 263: 'left', 264: 'down', 265: 'up',
    266: 'pageup', 267: 'pagedown', 268: 'home', 269: 'end',
    280: 'capslock', 281: 'scrolllock', 282: 'numlock',
    283: 'printscreen', 284: 'pause',
    290: 'f1', 291: 'f2', 292: 'f3', 293: 'f4', 294: 'f5', 295: 'f6',
    296: 'f7', 297: 'f8', 298: 'f9', 299: 'f10', 300: 'f11', 301: 'f12',
    302: 'f13', 303: 'f14', 304: 'f15', 305: 'f16', 306: 'f17',
    307: 'f18', 308: 'f19',
    320: 'num0', 321: 'num1', 322: 'num2', 323: 'num3', 324: 'num4',
    325: 'num5', 326: 'num6', 327: 'num7', 328: 'num8', 329: 'num9',
    330: 'decimal', 331: 'divide', 332: 'multiply', 333: 'subtract',
    334: 'add', 335: 'enter',
    340: 'shiftleft', 341: 'ctrlleft', 342: 'altleft', 343: 'winleft',
    344: 'shiftright', 345: 'ctrlright', 346: 'altright', 347: 'winright',
}


def keycode_to_pyautogui(key: int) -> Optional[str]:
    """Translate a raw window-backend keycode into a pyautogui key name."""
    if key in _NAMED_KEY_MAP:
        return _NAMED_KEY_MAP[key]
    if 32 <= key <= 126:
        ch = chr(key)
        return ch.lower() if ch.isalpha() else ch
    return None


# Printable ASCII key range, matching keycode_to_pyautogui's fallback branch.
_PRINTABLE_ASCII_COUNT = 126 - 32 + 1  # 95

DESKTOP_CONTROL_INTERFACE_SPEC = {
    'keys_press': {
        'count': len(_NAMED_KEY_MAP) + _PRINTABLE_ASCII_COUNT,
        'hz': 'event-driven, not polled -- one token per key-down. '
             'Human typing rarely exceeds ~10/s.',
    },
    'keys_release': {
        'count': len(_NAMED_KEY_MAP) + _PRINTABLE_ASCII_COUNT,
        'hz': 'event-driven -- one token per key-up, roughly mirrors keys_press.',
    },
    'mouse_move': {
        'count': 2,  # x, y -- continuous position, not discrete tokens
        'hz': 'event-driven, can be dense during drags -- '
             '30-60/s typical if the source polls continuously.',
    },
    'mouse_press': {
        'count': 3,  # left, right, middle
        'hz': 'event-driven, rare -- well under 10/s.',
    },
    'mouse_release': {
        'count': 3,
        'hz': 'event-driven, rare -- mirrors mouse_press.',
    },
    'scroll': {
        'count': 1,  # delta -- continuous, not a discrete token
        'hz': 'event-driven, rare -- well under 10/s.',
    },
}


def describe_control_interface() -> str:
    """Human-readable summary of DESKTOP_CONTROL_INTERFACE_SPEC, logged
    when a desktop connection starts so the interface shape is visible."""
    lines = ['Desktop control interface:']
    for channel, info in DESKTOP_CONTROL_INTERFACE_SPEC.items():
        lines.append(f"  {channel}: {info['count']} -- {info['hz']}")
    return '\n'.join(lines)


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

    def _on_mouse_move(self, x: float, y: float):
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=0, x=int(x), y=int(y), button=0, delta=0))

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
