"""
robonet/desktop_control_spec.py

Shared between robonet/brain/desktop_system.py (sends key/mouse events)
and robonet/endpoint/desktop_hardware.py (declares them as capabilities
axes) so both sides agree on the same channel counts.

Special-key mapping is built dynamically from the actual runtime
backend's key constants (moderngl_window's Keys object), not hardcoded
numbers -- those differ enormously between backends (pyglet's F4 is
0xffc1, GLFW's is 293), so a table hardcoded for one backend silently
fails to recognize special keys under a different one.
"""

from typing import Any, Dict, Optional

# moderngl_window.context.base.keys.BaseKeys attribute name -> pyautogui
# key name. Covers every named key BaseKeys defines except plain
# letters/digits, which map to their ASCII value consistently across
# backends already (see keycode_to_pyautogui's fallback branch).
_SPECIAL_KEY_TO_PYAUTOGUI = {
    'ESCAPE': 'esc', 'SPACE': 'space', 'ENTER': 'enter',
    'PAGE_UP': 'pageup', 'PAGE_DOWN': 'pagedown',
    'LEFT': 'left', 'RIGHT': 'right', 'UP': 'up', 'DOWN': 'down',
    'LEFT_SHIFT': 'shiftleft', 'RIGHT_SHIFT': 'shiftright', 'LEFT_CTRL': 'ctrlleft',
    'TAB': 'tab', 'COMMA': ',', 'MINUS': '-', 'PERIOD': '.', 'SLASH': '/',
    'SEMICOLON': ';', 'EQUAL': '=', 'LEFT_BRACKET': '[', 'RIGHT_BRACKET': ']',
    'BACKSLASH': '\\', 'BACKSPACE': 'backspace', 'INSERT': 'insert', 'DELETE': 'delete',
    'HOME': 'home', 'END': 'end', 'CAPS_LOCK': 'capslock',
    'F1': 'f1', 'F2': 'f2', 'F3': 'f3', 'F4': 'f4', 'F5': 'f5', 'F6': 'f6',
    'F7': 'f7', 'F8': 'f8', 'F9': 'f9', 'F10': 'f10', 'F11': 'f11', 'F12': 'f12',
}

_dynamic_map_cache: Dict[int, Dict[Any, str]] = {}


def _build_dynamic_key_map(keys) -> Dict[Any, str]:
    """keys -> {raw_value: pyautogui_name}, cached per keys object (its
    constant values don't change during a session)."""
    cache_key = id(keys)
    if cache_key in _dynamic_map_cache:
        return _dynamic_map_cache[cache_key]
    mapping = {}
    for attr_name, pyautogui_name in _SPECIAL_KEY_TO_PYAUTOGUI.items():
        value = getattr(keys, attr_name, None)
        if value is not None and value != 'undefined':
            mapping[value] = pyautogui_name
    _dynamic_map_cache[cache_key] = mapping
    return mapping


def keycode_to_pyautogui(key: int, keys=None) -> Optional[str]:
    """Translate a raw window-backend keycode into a pyautogui key name.

    keys: the backend's moderngl_window Keys object (e.g.
    displayer.displayer.displayer.config.wnd.keys). Without it, only
    plain ASCII letters/digits/punctuation (32-126) resolve -- special
    keys (F-keys, arrows, etc.) can't be identified at all since their
    raw values are backend-specific.
    """
    if keys is not None:
        dynamic_map = _build_dynamic_key_map(keys)
        if key in dynamic_map:
            return dynamic_map[key]
    if 32 <= key <= 126:
        ch = chr(key)
        return ch.lower() if ch.isalpha() else ch
    return None


DESKTOP_CONTROL_INTERFACE_SPEC = {
    'keys_press': {
        'count': len(_SPECIAL_KEY_TO_PYAUTOGUI) + 95,  # + printable ASCII (32-126)
        'hz': '~10/s',
    },
    'keys_release': {
        'count': len(_SPECIAL_KEY_TO_PYAUTOGUI) + 95,
        'hz': '~10/s',
    },
    'mouse_move_x': {
        'count': 1,
        'hz': '~30-60/s',
    },
    'mouse_move_y': {
        'count': 1,
        'hz': '30-60/s',
    },
    'mouse_press': {
        'count': 3,  # left, right, middle
        'hz': '<10/s',
    },
    'mouse_release': {
        'count': 3,
        'hz': '<10/s',
    },
    'scroll': {
        'count': 1,  # delta -- continuous, not a discrete token
        'hz': '<10/s',
    },
}


def describe_control_interface() -> str:
    """Human-readable summary of DESKTOP_CONTROL_INTERFACE_SPEC."""
    lines = ['Desktop control interface:']
    for channel, info in DESKTOP_CONTROL_INTERFACE_SPEC.items():
        lines.append(f"  {channel}: {info['count']} -- {info['hz']}")
    return '\n'.join(lines)
