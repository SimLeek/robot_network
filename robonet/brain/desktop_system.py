"""
robonet/brain/desktop_system.py

Forwards a human's raw keyboard and mouse input to the connected endpoint as
KeyEvent/MouseEvent bursts.
"""

from __future__ import annotations

import typing
import asyncio
from typing import Dict, Optional

from robonet.brain.util.action_factory import ActionFactory
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
    AI_SUPPORTED_KEYS,
)

# AI passthrough: neuron/token indices an AI uses to drive mouse input
# through ActionFactory's existing bind_ai_neuron/bind_ai_token
# mechanism. Mouse position is continuous (2 neurons, threshold=0 so
# any provided value takes effect immediately -- fractions are always
# >= 0). Buttons are discrete one-shot triggers (tokens). Keyboard
# isn't listed here -- too many possible keys for a fixed token enum --
# see ai_key_press/ai_key_release instead.
# Key hold watchdog: lossy networks drop KeyEvents. The brain re-sends key-down
# for every held key at this interval; the endpoint auto-releases any
# key not refreshed within its KEY_WATCHDOG_TIMEOUT_S (1.0s = 4 missed
# refreshes).
KEY_REFRESH_INTERVAL_S = 0.25

AI_NEURON_MOUSE_X = 0
AI_NEURON_MOUSE_Y = 1
AI_TOKEN_MOUSE_LEFT_PRESS = 0
AI_TOKEN_MOUSE_LEFT_RELEASE = 1
AI_TOKEN_MOUSE_RIGHT_PRESS = 2
AI_TOKEN_MOUSE_RIGHT_RELEASE = 3
AI_TOKEN_MOUSE_MIDDLE_PRESS = 4
AI_TOKEN_MOUSE_MIDDLE_RELEASE = 5
AI_TOKEN_ZOOM_IN = 6
AI_TOKEN_ZOOM_OUT = 7
AI_TOKEN_PAN_LEFT = 8
AI_TOKEN_PAN_RIGHT = 9
AI_TOKEN_PAN_UP = 10
AI_TOKEN_PAN_DOWN = 11
AI_TOKEN_VIEW_RESET = 12

AI_ZOOM_FACTOR = 1.1   # per zoom token, matching the human scroll step
AI_PAN_STEP = 0.05     # per pan token, as a fraction of the visible view

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

        self._screen_width, self._screen_height = 1920, 1080
        for s in getattr(endpoint, 'streams', None) or []:
            if s.get('name') == 'screen' and s.get('type') == 'video':
                self._screen_width = s.get('width', self._screen_width)
                self._screen_height = s.get('height', self._screen_height)
                break

        self.viewport = Viewport()
        self._edit_mouse_pos: Optional[tuple] = None

        # AI passthrough: whichever source is active gets its input
        # sent; the other's is silently dropped. Both a human and an AI
        # trying to drive the same remote cursor at once would just
        # fight each other, so only one is ever actually forwarded.
        # Works with no display at all (an AI-only session) since
        # nothing here depends on self._root.displayer.
        self.input_source = 'human'
        self._ai_mouse_x, self._ai_mouse_y = 0.5, 0.5
        self._held_keys_brain: dict = {}   # key name -> modifiers string, for the refresh loop
        self._running = False
        # Independent of DisplaySubSystem's af_thru/af_edit, which only
        # exist when a display window does. AI passthrough works with
        # no display at all.
        self.af_ai = ActionFactory()

    def setup(self, root: 'ServerSystem'):
        self._root = root

    def start(self):
        self._running = True
        self._bind_input()
        self._log_action_space_size()
        log.info(describe_control_interface())

    def stop(self):
        self._running = False
        self._unbind_input()

    # -- input forwarding ----------------------------------------------

    def _bind_input(self):
        self.af_ai.bind_ai_neuron(self._ai_on_mouse_x, AI_NEURON_MOUSE_X, threshold=0.0)
        self.af_ai.bind_ai_neuron(self._ai_on_mouse_y, AI_NEURON_MOUSE_Y, threshold=0.0)
        self.af_ai.bind_ai_token(lambda: self.ai_mouse_press('left'), AI_TOKEN_MOUSE_LEFT_PRESS)
        self.af_ai.bind_ai_token(lambda: self.ai_mouse_release('left'), AI_TOKEN_MOUSE_LEFT_RELEASE)
        self.af_ai.bind_ai_token(lambda: self.ai_mouse_press('right'), AI_TOKEN_MOUSE_RIGHT_PRESS)
        self.af_ai.bind_ai_token(lambda: self.ai_mouse_release('right'), AI_TOKEN_MOUSE_RIGHT_RELEASE)
        self.af_ai.bind_ai_token(lambda: self.ai_mouse_press('middle'), AI_TOKEN_MOUSE_MIDDLE_PRESS)
        self.af_ai.bind_ai_token(lambda: self.ai_mouse_release('middle'), AI_TOKEN_MOUSE_MIDDLE_RELEASE)
        self.af_ai.bind_ai_token(lambda: self.ai_zoom(AI_ZOOM_FACTOR), AI_TOKEN_ZOOM_IN)
        self.af_ai.bind_ai_token(lambda: self.ai_zoom(1.0 / AI_ZOOM_FACTOR), AI_TOKEN_ZOOM_OUT)
        self.af_ai.bind_ai_token(lambda: self.ai_pan(-AI_PAN_STEP, 0.0), AI_TOKEN_PAN_LEFT)
        self.af_ai.bind_ai_token(lambda: self.ai_pan(AI_PAN_STEP, 0.0), AI_TOKEN_PAN_RIGHT)
        self.af_ai.bind_ai_token(lambda: self.ai_pan(0.0, -AI_PAN_STEP), AI_TOKEN_PAN_UP)
        self.af_ai.bind_ai_token(lambda: self.ai_pan(0.0, AI_PAN_STEP), AI_TOKEN_PAN_DOWN)
        self.af_ai.bind_ai_token(self.ai_view_reset, AI_TOKEN_VIEW_RESET)

        if self._root.displayer is None:
            self._bound = True
            return  # AI-direct-only session: no window to bind human input to
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

    def _log_action_space_size(self):
        """Knowing the actual action-space size is mandatory for
        plugging in an RL/neural-net-style AI -- and there was
        previously no way to get it at all. Reports bound tokens
        (discrete, e.g. mouse press/release, zoom/pan/reset), bound
        neurons (continuous, e.g. mouse x/y), and AI_SUPPORTED_KEYS --
        the exact, curated list ai_key_press/release accept, so
        developers aren't guessing whether e.g. f11 actually works vs.
        some pyautogui extra unreachable through this system. Keys are
        still an open string, not a fixed token index each; if/when
        they become fixed tokens, a natural mapping is one token per
        key with a threshold-gated neuron (0/1, or -1/1, or a 0.5
        crossing) rather than separate press/release tokens per key --
        flagged here rather than decided unilaterally."""
        n_tokens = len(self.af_ai._token_to_handlers)
        n_neurons = len(self.af_ai._neuron_to_handler_thresholds)
        log.info(f'[desktop] AI action space: {n_tokens} discrete tokens, '
                f'{n_neurons} continuous neurons, {len(AI_SUPPORTED_KEYS)} supported '
                f'keys (ai_key_press/release take one of these exact names -- '
                f'see robonet.desktop_control_spec.AI_SUPPORTED_KEYS): '
                f'{AI_SUPPORTED_KEYS}')

    def _unbind_input(self):
        if not self._bound:
            return
        self.af_ai.unbind_all()
        if self._root is None or self._root.displayer is None:
            self._bound = False
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
        if d is None:
            return
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
        mods_s = ','.join(mods)
        pressed = (action == wkeys.ACTION_PRESS)
        # Track the physical hold BEFORE any transmit gating: a release
        # swallowed because the menu opened mid-hold (or the input
        # source switched) must still stop the refresh loop, so the
        # endpoint's watchdog releases the key within its timeout.
        if pressed:
            self._held_keys_brain[name] = mods_s
        else:
            self._held_keys_brain.pop(name, None)
        if self.input_source != 'human':
            return
        if self._root.menu.visible:
            return  # don't send control while the menu is open
        self._root.radio.burst(KeyEvent(key=name, pressed=pressed, modifiers=mods_s))

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
        if self.input_source != 'human':
            return
        if self._root.displayer is None or self._root.menu.visible:
            return
        x, y = self._frac_to_pixel(tx, ty)
        self._root.radio.burst(MouseEvent(event_type=0, x=x, y=y, button=0, delta=0))

    def _on_mouse_press(self, tx: float, ty: float, button: int):
        if self.input_source != 'human':
            return
        if self._root.displayer is None or self._root.menu.visible:
            return
        x, y = self._frac_to_pixel(tx, ty)
        self._root.radio.burst(MouseEvent(event_type=1, x=x, y=y, button=int(button), delta=0))

    def _on_mouse_release(self, tx: float, ty: float, button: int):
        if self.input_source != 'human':
            return
        if self._root.displayer is None or self._root.menu.visible:
            return
        x, y = self._frac_to_pixel(tx, ty)
        self._root.radio.burst(MouseEvent(event_type=2, x=x, y=y, button=int(button), delta=0))

    def _on_mouse_scroll(self, y_offset: int):
        if self.input_source != 'human':
            return
        if self._root.displayer is None or self._root.menu.visible:
            return
        self._root.radio.burst(MouseEvent(event_type=3, x=0, y=0, button=0, delta=int(y_offset)))

    # -- AI passthrough: works with or without a display -------------
    #
    # The zoom/pan/letterbox viewport exists FOR the AI: it can only
    # ingest ~800x600-class frames, while the real screen is 1080p+,
    # so without zoom/pan it literally cannot see enough detail to
    # interact. send_frames_always applies the shared viewport to the
    # frame BOTH the human display and the AI receive -- so the AI's
    # coordinates mean positions within that same zoomed/panned/
    # letterboxed canvas and route through the same inverse mapping as
    # human input. (0,0)-(1,1) spans the canvas the AI actually sees,
    # not the raw screen. The AI drives the viewport itself through
    # the zoom/pan tokens below, exactly like a human uses edit mode.

    _AI_BUTTON_NAMES = {1: 'left', 4: 'right', 2: 'middle'}  # must match desktop_hardware.py's _MOUSE_BUTTON_NAMES exactly (pyglet bitmask values)

    def set_input_source(self, source: str):
        """'human' or 'ai' -- whichever is active gets its input sent;
        the other's is silently dropped rather than fighting over the
        same remote cursor."""
        assert source in ('human', 'ai'), f"input_source must be 'human' or 'ai', got {source!r}"
        self.input_source = source

    def _ai_frac_to_pixel(self, x_frac: float, y_frac: float) -> tuple:
        """Canvas fractions (within the same viewport-processed frame
        the AI receives) -> true screen pixels, via the same inverse
        mapping the human path uses. Uses menu.out_res rather than the
        displayer's so headless AI-only sessions work identically."""
        x_frac = max(0.0, min(1.0, x_frac))
        y_frac = max(0.0, min(1.0, y_frac))
        dims = self._viewport_dims()
        if dims is not None:
            source_w, source_h, display_w, display_h = dims
            x_frac, y_frac = self.viewport.inverse_map(
                x_frac, y_frac, source_w, source_h, display_w, display_h)
        x = max(0, int(x_frac * self._screen_width))
        y = max(0, int(y_frac * self._screen_height))
        return x, y

    def _viewport_dims(self):
        m = self._root.menu
        img = getattr(m, 'last_img', None) if m is not None else None
        if img is None or not hasattr(img, 'shape'):
            return None  # no frame yet (or a test double) -- treat as identity
        source_h, source_w = img.shape[:2]
        display_w, display_h = m.out_res
        return source_w, source_h, display_w, display_h

    # -- AI view controls: the same zoom/pan a human gets in edit
    # mode, driven through tokens. Gated on input_source like the rest
    # of the AI's actions since the viewport is currently SHARED with
    # the human display -- an AI panning around mid-human-session would
    # yank the human's view.

    def ai_zoom(self, factor: float):
        if self.input_source != 'ai':
            return
        dims = self._viewport_dims()
        if dims is None:
            return
        self.viewport.zoom_by(factor, *dims)

    def ai_pan(self, dx_frac: float, dy_frac: float):
        if self.input_source != 'ai':
            return
        dims = self._viewport_dims()
        if dims is None:
            return
        self.viewport.pan_by(dx_frac, dy_frac, *dims)

    def ai_view_reset(self):
        if self.input_source != 'ai':
            return
        self.viewport.reset()

    def _ai_on_mouse_x(self, value: float):
        self._ai_mouse_x = value
        self.ai_mouse_move(self._ai_mouse_x, self._ai_mouse_y)

    def _ai_on_mouse_y(self, value: float):
        self._ai_mouse_y = value
        self.ai_mouse_move(self._ai_mouse_x, self._ai_mouse_y)

    def ai_mouse_move(self, x_frac: float, y_frac: float):
        if self.input_source != 'ai':
            return
        self._ai_mouse_x, self._ai_mouse_y = x_frac, y_frac
        x, y = self._ai_frac_to_pixel(x_frac, y_frac)
        self._root.radio.burst(MouseEvent(event_type=0, x=x, y=y, button=0, delta=0))

    def ai_mouse_press(self, button: str = 'left'):
        if self.input_source != 'ai':
            return
        code = {v: k for k, v in self._AI_BUTTON_NAMES.items()}.get(button, 1)
        x, y = self._ai_frac_to_pixel(self._ai_mouse_x, self._ai_mouse_y)
        self._root.radio.burst(MouseEvent(event_type=1, x=x, y=y, button=code, delta=0))

    def ai_mouse_release(self, button: str = 'left'):
        if self.input_source != 'ai':
            return
        code = {v: k for k, v in self._AI_BUTTON_NAMES.items()}.get(button, 1)
        x, y = self._ai_frac_to_pixel(self._ai_mouse_x, self._ai_mouse_y)
        self._root.radio.burst(MouseEvent(event_type=2, x=x, y=y, button=code, delta=0))

    def ai_scroll(self, delta: int):
        if self.input_source != 'ai':
            return
        self._root.radio.burst(MouseEvent(event_type=3, x=0, y=0, button=0, delta=int(delta)))

    def ai_key_press(self, key: str, modifiers: str = ''):
        self._held_keys_brain[key] = modifiers  # tracked regardless of gating -- see _on_keyboard
        if self.input_source != 'ai':
            return
        self._root.radio.burst(KeyEvent(key=key, pressed=True, modifiers=modifiers))

    def ai_key_release(self, key: str, modifiers: str = ''):
        self._held_keys_brain.pop(key, None)
        if self.input_source != 'ai':
            return
        self._root.radio.burst(KeyEvent(key=key, pressed=False, modifiers=modifiers))

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

    def _refresh_held_keys(self):
        """One watchdog tick: re-send key-down for every held key so
        the endpoint knows the hold is still real. See
        KEY_REFRESH_INTERVAL_S for why."""
        for name, mods_s in list(self._held_keys_brain.items()):
            self._root.radio.burst(KeyEvent(key=name, pressed=True, modifiers=mods_s))

    async def _key_refresh_loop(self):
        while self._running:
            self._refresh_held_keys()
            await asyncio.sleep(KEY_REFRESH_INTERVAL_S)

    def async_loops(self, sm: 'ServerSystem'):
        return [self._key_refresh_loop()]  # purely event-driven -- no polling loop needed
