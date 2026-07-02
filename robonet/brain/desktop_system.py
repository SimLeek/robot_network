"""
robonet/brain/desktop_system.py

Brain-side counterpart to a 'desktop' endpoint (DesktopHw). Forwards the
human's raw keyboard and mouse input to the connected endpoint as
KeyEvent/MouseEvent bursts. Unlike RobotSubSystem's sparse axis-vector
model (keys mapped to declared control axes, polled at a fixed rate),
desktop control is direct, event-driven pass-through: there is no axis
mapping, every raw input event becomes one burst.
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

# Best-effort GLFW keycode -> pyautogui key name mapping (moderngl-window,
# which displayarray's input_mgl backend wraps, uses GLFW key constants).
# Printable ASCII keys (32-126) map directly via chr() and don't need an
# entry here -- this table only covers the named/non-printable keys.
# Verify against the actual windowing backend in your environment; this
# was written from the standard GLFW key constant list, not verified
# against a live display here.
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
    """Translate a raw window-backend keycode into a pyautogui key name,
    or None if there's no reasonable mapping (the event is then dropped)."""
    if key in _NAMED_KEY_MAP:
        return _NAMED_KEY_MAP[key]
    if 32 <= key <= 126:
        ch = chr(key)
        return ch.lower() if ch.isalpha() else ch
    return None


# Printable ASCII key range, matching keycode_to_pyautogui's fallback branch.
_PRINTABLE_ASCII_COUNT = 126 - 32 + 1  # 95

# The control-interface size an AI would need to fully drive a desktop
# endpoint, were bind_ai_token/bind_ai_neuron wired up to it -- not done
# yet, AI control is explicitly the last step for this whole project.
# This documents the target shape ahead of that work, computed from the
# actual key table above rather than hand-counted so it can't drift out
# of sync with it.
#
# Each channel has a very different natural rate -- unlike a robot's
# fixed-Hz polled axis vector, desktop control is event-driven per
# channel, so Hz is documented per channel rather than as one number for
# the whole endpoint (a key press and a mouse-move stream don't remotely
# share a natural rate).
#
# For comparison, a robot endpoint's neuron count is len(ep.axes) --
# already available on any connected Endpoint (see RobotCapabilities),
# each axis polled uniformly at RobotSubSystem.CTRL_HZ.
DESKTOP_CONTROL_INTERFACE_SPEC = {
    'keys_press': {
        'count': len(_NAMED_KEY_MAP) + _PRINTABLE_ASCII_COUNT,
        'hz': 'event-driven, not polled -- one token per key-down. '
             'Human typing rarely exceeds ~10/s; no hard ceiling.',
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
    when a desktop connection starts so the interface shape is visible
    without having to go read this file."""
    lines = ['Desktop control interface (not yet wired to an AI -- human '
            'pass-through only for now):']
    for channel, info in DESKTOP_CONTROL_INTERFACE_SPEC.items():
        lines.append(f"  {channel}: {info['count']} -- {info['hz']}")
    return '\n'.join(lines)


class DesktopSubSystem(SubSystem):
    """Server-side desktop counterpart.

    Handshake: identical to RobotSubSystem (handled by RadioSubSystem);
    this class only takes over once WhoAreYouAck/RobotCapabilities have
    already completed and MenuSubSystem has swapped it in.

    Control: every raw keyboard/mouse event from the human, forwarded
    immediately as a KeyEvent or MouseEvent burst. No polling loop.
    """
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
            return  # AI-driven session: no local human input to forward (yet)
        af = self._root.displayer.af_thru
        af.bind_keyboard(self._on_keyboard)
        af.bind_mouse_move(self._on_mouse_move)
        af.bind_mouse_click(self._on_mouse_click)
        af.bind_mouse_scroll(self._on_mouse_scroll)
        self._bound = True

    def _unbind_input(self):
        if not self._bound or self._root is None or self._root.displayer is None:
            return
        af = self._root.displayer.af_thru
        af.unbind_keyboard()
        af.unbind_mouse_move()
        af.unbind_mouse_click()
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

    def _on_mouse_click(self, x: float, y: float, button: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        # ActionFactory's mouse-click callback only fires on button-down --
        # there is currently no separate release event coming through the
        # pass-through window config (see desktop_window_config.py). Send
        # press immediately followed by release so a normal click works;
        # click-and-drag will need that callback extended to distinguish
        # down from up before it can work too.
        self._root.radio.burst(MouseEvent(event_type=1, x=int(x), y=int(y), button=int(button), delta=0))
        self._root.radio.burst(MouseEvent(event_type=2, x=int(x), y=int(y), button=int(button), delta=0))

    def _on_mouse_scroll(self, y_offset: int):
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=3, x=0, y=0, button=0, delta=int(y_offset)))

    def async_loops(self, sm: 'ServerSystem'):
        return []  # purely event-driven -- no polling loop needed
