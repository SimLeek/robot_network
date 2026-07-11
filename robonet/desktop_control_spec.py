"""
robonet/desktop_control_spec.py

Shared between robonet/brain/desktop_system.py (sends key/mouse events)
and robonet/endpoint/desktop_hardware.py (declares them as capabilities
axes) so both sides agree on the same channel counts.
"""

from typing import Dict, Optional

# Best-effort GLFW keycode -> pyautogui key name mapping.
# Printable ASCII keys (32-126) map directly via chr() already.
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
    'mouse_move_x': {
        'count': 1,
        'hz': 'event-driven, can be dense during drags -- '
             '30-60/s typical if the source polls continuously.',
    },
    'mouse_move_y': {
        'count': 1,
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
    """Human-readable summary of DESKTOP_CONTROL_INTERFACE_SPEC."""
    lines = ['Desktop control interface:']
    for channel, info in DESKTOP_CONTROL_INTERFACE_SPEC.items():
        lines.append(f"  {channel}: {info['count']} -- {info['hz']}")
    return '\n'.join(lines)
