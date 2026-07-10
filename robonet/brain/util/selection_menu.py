# Todo: Needs Testing

import asyncio
import json
import time
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from robonet.brain.util.bitmapfont import cols_for_width, wrap_text
from robonet.brain.util.network_scanner import Endpoint
from robonet.buffers.buffer_objects import SelectAVSource
from statemachine import StateChart, State
from robonet.brain.util.bitmapfont import render_text


class MenuVisState(Enum):
    """Top-level visibility state."""
    HIDDEN      = auto()
    MAIN        = auto()
    SUDO_PROMPT = auto()


class MenuStateMachine(StateChart):
    """Full menu state machine -- defines the sub-menu hierarchy."""

    main_menu     = State(initial=True)
    radio_menu    = State()
    settings_menu = State()
    capabilities_menu = State()
    av_sources_menu   = State()

    # escape / back
    leave    = (radio_menu.to(main_menu) | settings_menu.to(main_menu)
               | capabilities_menu.to(main_menu) | av_sources_menu.to(main_menu))
    # enter / select
    radio        = main_menu.to(radio_menu)
    settings     = main_menu.to(settings_menu)
    capabilities = main_menu.to(capabilities_menu)
    av_sources   = main_menu.to(av_sources_menu)


# ---------------------------------------------------------------------------
# Palette (BGR, matching OpenCV convention used by displayarray)
# ---------------------------------------------------------------------------
_BG    = (20,  20,  20)
_FG    = (220, 220, 220)
_SEL   = (80,  160, 255)
_TITLE = (120, 200, 255)
_WARN  = (200,  80,  80)
_DIM   = (120, 120, 120)
_KEY   = (100, 220, 130)   # axis key binding highlight

_MAIN_ITEMS_BASE = ['Radio', 'Settings']
_CAPS_PAGES = ['Axes', 'Streams']
_AV_SOURCE_KINDS = ['video', 'audio_in', 'audio_out']
_AV_SOURCE_KIND_LABELS = {'video': 'Video', 'audio_in': 'Audio In', 'audio_out': 'Audio Out'}

def _fmt_keycode(kc: int) -> str:
    if 32 <= kc < 127:
        return chr(kc)
    return f'#{kc}'

def _fmt_axis(a: dict) -> str:
    name = a.get('name', '?')
    desc = a.get('description', '')
    keys = a.get('keys', [])
    key_str = '/'.join(_fmt_keycode(int(kv[0])) for kv in keys) if keys else '-'
    neuron  = a.get('neuron', '?')
    # Trim desc so the whole line stays readable at small widths
    line = f'[{key_str}] {name}  #{neuron}'
    if desc:
        line += f'  {desc}'
    return line

def _fmt_stream(s: dict) -> str:
    name = s.get('name', '?')
    t    = s.get('type', '?')
    if 'mjpeg' in t or 'video' in t:
        w, h = s.get('width', '?'), s.get('height', '?')
        return f'{name}  {t}  {w}x{h}'
    if 'audio' in t:
        sr = s.get('sample_rate', '?')
        ch = s.get('channels',    '?')
        return f'{name}  {t}  {sr}Hz  ch{ch}'
    return f'{name}  {t}'

class SelectionMenu:
    """
    Scrollable, cursor-centred overlay menu.

    The currently selected item is always rendered at vertical centre.
    Items above and below fill the remaining screen space until the
    edge is reached.
    """

    def __init__(self, width: int = 320, height: int = 240,
                 sudo_cb: Optional[Callable[[str], None]] = None,
                 font_scale: int = 1,
                 settings=None):
        self.width      = width
        self.height     = height
        self.sudo_cb    = sudo_cb
        self.font_scale = font_scale
        self.state      = MenuVisState.MAIN
        self.menu_state = MenuStateMachine()
        self.root       = None
        self._settings  = settings   # optional pre-bound Settings; lazy-loads if None

        self._endpoints: Dict[str, Endpoint] = {}
        self._cursor    = 0
        self._password  = ''
        self._sudo_desc = ''
        self._status    = ''
        self._status_ts = 0.0

        # Settings edit state
        self._settings_edit = False
        self._edit_key      = ''
        self._edit_buffer   = ''

        # Endpoint capabilities state (axes/streams)
        self._endpoint_caps: Optional[dict] = None   # {'axes': [...], 'streams': [...]}
        self._endpoint_page: int = 0                  # index into _CAPS_PAGES

        # AV source selection state
        self._av_sources = None   # most recent AVSourcesAnnounce, or None
        self._av_kind_index: int = 0   # index into _AV_SOURCE_KINDS

        # Derived layout constants
        self._ch   = 8 * font_scale + 2   # character row height in pixels
        self._cols = cols_for_width(width, font_scale)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def visible(self) -> bool:
        return self.state != MenuVisState.HIDDEN

    @property
    def _cfg(self):
        """Lazy-load the Settings singleton if none was injected."""
        if self._settings is not None:
            return self._settings
        import robonet.brain.settings as _s
        return _s.get()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def toggle(self):
        self.state = (MenuVisState.MAIN
                      if self.state == MenuVisState.HIDDEN
                      else MenuVisState.HIDDEN)

    def set_endpoints(self, endpoints: Dict[str, Endpoint]):
        self._endpoints = endpoints
        self._cursor = min(self._cursor, max(0, len(endpoints) - 1))

    def set_status(self, msg: str):
        self._status    = msg
        self._status_ts = time.time()

    def set_endpoint_capabilities(self, caps: dict):
        self._endpoint_caps = caps
        self._endpoint_page = 0

    def set_av_sources(self, announce):
        """Called by MenuSubSystem whenever an AVSourcesAnnounce arrives"""
        self._av_sources = announce
        self._av_kind_index = 0

    def clear_av_sources(self):
        """Called on disconnect so a stale announce from a previous endpoint doesn't linger in the menu."""
        self._av_sources = None
        self._av_kind_index = 0

    def request_sudo(self, description: str):
        self._sudo_desc = description
        self._password  = ''
        self.state      = MenuVisState.SUDO_PROMPT

    def on_char(self, char: str):
        if self.state == MenuVisState.SUDO_PROMPT:
            self._password += char
        elif (self.state == MenuVisState.MAIN
              and self.menu_state.settings_menu.is_active
              and self._settings_edit):
            self._edit_buffer += char

    def on_key(self, key: str) -> Optional[Endpoint]:
        """
        key: 'enter' | 'backspace' | 'escape' | 'up' | 'down'

        Returns the selected Endpoint when the user confirms a connection
        in the radio menu, otherwise None.
        """
        if self.state == MenuVisState.SUDO_PROMPT:
            return self._handle_sudo_key(key)

        if self.state == MenuVisState.MAIN:
            if self.menu_state.main_menu.is_active:
                self._handle_main_key(key)
            elif self.menu_state.radio_menu.is_active:
                return self._handle_radio_key(key)
            elif self.menu_state.settings_menu.is_active:
                self._handle_settings_key(key)
            elif self.menu_state.capabilities_menu.is_active:
                self._handle_capabilities_key(key)
            elif self.menu_state.av_sources_menu.is_active:
                self._handle_av_sources_key(key)

        return None

    def _main_items(self) -> List[str]:
        """Main menu items."""
        items = list(_MAIN_ITEMS_BASE)
        if self._endpoint_caps is not None:
            items.append('Capabilities')
        if self._av_sources is not None:
            items.append('AV Sources')
        return items

    # ------------------------------------------------------------------
    # Key handlers
    # ------------------------------------------------------------------

    def _handle_sudo_key(self, key: str) -> None:
        if key == 'backspace':
            self._password = self._password[:-1]
        elif key == 'escape':
            self.state = MenuVisState.MAIN
        elif key == 'enter':
            pw, self._password = self._password, ''
            self.state = MenuVisState.MAIN
            if self.sudo_cb:
                self.sudo_cb(pw)
        return None

    def _handle_main_key(self, key: str):
        items = self._main_items()
        n = len(items)
        if key == 'up':
            self._cursor = max(0, self._cursor - 1)
        elif key == 'down':
            self._cursor = min(n - 1, self._cursor + 1)
        elif key == 'escape':
            self.state = MenuVisState.HIDDEN
        elif key == 'enter':
            label = items[self._cursor]
            self.menu_state.send(label.lower().replace(' ', '_'))
            self._cursor = 0  # fresh cursor for the sub-menu

    # --- radio ---------------------------------------------------------

    def get_unique_endpoints(self):
        seen_ids = set()
        unique_endpoints = []

        for ep in self._endpoints.values():
            obj_id = id(ep)  # Or ep.unique_id if your class has one
            if obj_id not in seen_ids:
                unique_endpoints.append(ep)
                seen_ids.add(obj_id)
        return unique_endpoints

    def _radio_items(self) -> List[str]:
        """Build the current radio menu item list."""
        mode    = self.root.radio.mode
        NetMode = self.root.radio.NetMode
        chk     = lambda active: '[X]' if active else '[ ]'
        items = [
            f'Mode: Local   {chk(mode == NetMode.LOCALHOST)}',
            f'Mode: Wi-Fi   {chk(mode == NetMode.WIFI)}',
            f'Mode: Ad-Hoc  {chk(mode == NetMode.ADHOC)}',
            f'Mode: Wired   {chk(mode == NetMode.WIRED)}',
            'Stop Scanning' if self.root.radio.is_scanning else 'Start Scanning',
        ]
        for ep in self.get_unique_endpoints():
            ready = bool(getattr(ep, 'axes', None) or getattr(ep, 'streams', None))
            tag = ep.endpoint_type or 'unknown'
            label = f'{"[ready]" if ready else "[..]"} {ep.ip}  [{tag}]'
            if ep.hostname:
                label += f'  {ep.hostname}'
            if not ready:
                label += '  (discovering...)'
            items.append(label)
        return items

    def _handle_radio_key(self, key: str) -> Optional[Endpoint]:
        items     = self._radio_items()
        endpoints = self.get_unique_endpoints()
        n         = len(items)

        if key == 'up':
            self._cursor = max(0, self._cursor - 1)
        elif key == 'down':
            self._cursor = min(n - 1, self._cursor + 1)
        elif key == 'escape':
            self.menu_state.send('leave')
            self._cursor = 0
        elif key == 'enter' and items:
            ci = self._cursor
            if ci == 0:
                asyncio.ensure_future(
                    self.root.radio.switch_mode(self.root.radio.NetMode.LOCALHOST))
            elif ci == 1:
                asyncio.ensure_future(
                    self.root.radio.switch_mode(self.root.radio.NetMode.WIFI))
            elif ci == 2:
                asyncio.ensure_future(
                    self.root.radio.switch_mode(self.root.radio.NetMode.ADHOC))
            elif ci == 3:
                asyncio.ensure_future(
                    self.root.radio.switch_mode(self.root.radio.NetMode.WIRED))
            elif ci == 4:
                if self.root.radio.is_scanning:
                    asyncio.ensure_future(self.root.radio.stop_scanner_task())
                else:
                    asyncio.ensure_future(self.root.radio.start_scanner_task())
            else:
                ep_idx = ci - 5
                if 0 <= ep_idx < len(endpoints):
                    ep = endpoints[ep_idx]
                    if not (getattr(ep, 'axes', None) or getattr(ep, 'streams', None)):
                        self.set_status('Endpoint not ready yet - wait for capabilities')
                        return None
                    return ep
        return None

    # --- settings ------------------------------------------------------

    def _settings_items(self) -> List[Tuple[str, object, bool]]:
        """Return [(key, value, is_editable), ...] for every known setting."""
        return [(k, v, en) for k, (v, en) in self._cfg.items().items()]

    def _handle_settings_key(self, key: str):
        items = self._settings_items()
        n     = len(items)

        # -- editing a value --------------------------------------------
        if self._settings_edit:
            if key == 'backspace':
                self._edit_buffer = self._edit_buffer[:-1]
            elif key == 'escape':
                self._settings_edit = False
            elif key == 'enter':
                self._commit_edit()
            return

        # -- browsing ---------------------------------------------------
        if key == 'up':
            self._cursor = max(0, self._cursor - 1)
        elif key == 'down':
            self._cursor = min(n - 1, self._cursor + 1)
        elif key == 'escape':
            self.menu_state.send('leave')
            self._cursor = 0
        elif key == 'enter' and items:
            k, v, enabled = items[self._cursor]
            if not enabled:
                self.set_status('Advanced setting -- read-only')
                return
            if isinstance(v, bool):
                # bools toggle immediately; no text entry needed
                self._cfg[k] = not v
                self._cfg.save()
                self.set_status(f'{k} -> {not v}')
            else:
                self._edit_key    = k
                self._edit_buffer = str(v)
                self._settings_edit = True

    def _commit_edit(self):
        """Parse _edit_buffer into the correct type and persist."""
        k   = self._edit_key
        old = self._cfg[k]
        try:
            if isinstance(old, bool):
                new = self._edit_buffer.strip().lower() in ('true', '1', 'yes')
            elif isinstance(old, int):
                new = int(self._edit_buffer)
            elif isinstance(old, float):
                new = float(self._edit_buffer)
            elif isinstance(old, list):
                new = json.loads(self._edit_buffer)
            else:
                new = self._edit_buffer
            self._cfg[k] = new
            self._cfg.save()
            self.set_status(f'Saved {k}')
        except Exception as exc:
            self.set_status(f'Bad value: {exc}')
        finally:
            self._settings_edit = False

    def _capabilities_page_items(self) -> List[str]:
        if self._endpoint_caps is None:
            return ['[no capabilities received]']
        if self._endpoint_page == 0:
            return [_fmt_axis(a) for a in self._endpoint_caps.get('axes', [])] or ['[no axes]']
        else:
            return [_fmt_stream(s) for s in self._endpoint_caps.get('streams', [])] or ['[no streams]']

    def _handle_capabilities_key(self, key: str):
        n_pages = len(_CAPS_PAGES)
        items = self._capabilities_page_items()
        n = len(items)

        if key == 'escape':
            self.menu_state.send('leave')
            self._cursor = 0
            self._endpoint_page = 0
        elif key == 'left':
            self._endpoint_page = (self._endpoint_page - 1) % n_pages
            self._cursor = 0
        elif key == 'right':
            self._endpoint_page = (self._endpoint_page + 1) % n_pages
            self._cursor = 0
        elif key == 'up':
            self._cursor = max(0, self._cursor - 1)
        elif key == 'down':
            self._cursor = min(n - 1, self._cursor + 1)

    def _av_source_items(self) -> List[str]:
        if self._av_sources is None:
            return ['[no sources announced]']
        kind = _AV_SOURCE_KINDS[self._av_kind_index]
        ids = getattr(self._av_sources, f'{kind}_ids', [])
        active = getattr(self._av_sources, f'active_{kind}_id', '')
        if not ids:
            return ['[none available]']
        return [f'{"[*]" if sid == active else "[ ]"} {sid}' for sid in ids]

    def _handle_av_sources_key(self, key: str):
        n_kinds = len(_AV_SOURCE_KINDS)
        items = self._av_source_items()
        n = len(items)

        if key == 'escape':
            self.menu_state.send('leave')
            self._cursor = 0
            self._av_kind_index = 0
        elif key == 'left':
            self._av_kind_index = (self._av_kind_index - 1) % n_kinds
            self._cursor = 0
        elif key == 'right':
            self._av_kind_index = (self._av_kind_index + 1) % n_kinds
            self._cursor = 0
        elif key == 'up':
            self._cursor = max(0, self._cursor - 1)
        elif key == 'down':
            self._cursor = min(n - 1, self._cursor + 1)
        elif key == 'enter' and self._av_sources is not None:
            kind = _AV_SOURCE_KINDS[self._av_kind_index]
            ids = getattr(self._av_sources, f'{kind}_ids', [])
            if 0 <= self._cursor < len(ids):
                source_id = ids[self._cursor]
                if self.root is not None:
                    self.root.radio.burst(SelectAVSource(kind=kind, source_id=source_id))
                self.set_status(f'Selecting {kind} -> {source_id}')

    # ------------------------------------------------------------------
    # Compositing
    # ------------------------------------------------------------------

    def composite(self, frame: np.ndarray) -> np.ndarray:
        if self.state == MenuVisState.HIDDEN:
            return frame
        overlay = self._render()
        fh, fw  = frame.shape[:2]
        mh, mw  = overlay.shape[:2]
        ph, pw  = min(fh, mh), min(fw, mw)
        roi     = frame[:ph, :pw].astype(np.float32)
        rgb     = overlay[:ph, :pw, :3].astype(np.float32)
        alpha   = overlay[:ph, :pw, 3:4].astype(np.float32) / 255.0
        blended = (roi * (1.0 - alpha) + rgb * alpha).astype(np.uint8)
        out     = frame.copy()
        out[:ph, :pw] = blended
        return out

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> np.ndarray:
        img = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        img[:, :, :3] = _BG
        img[:, :, 3]  = 210
        if self.state == MenuVisState.MAIN:
            self._draw_main(img)
        elif self.state == MenuVisState.SUDO_PROMPT:
            self._draw_sudo(img)
        return img

    def _blit_line(self, img, y: int, text: str,
                   color=_FG, bg=None) -> int:
        """Blit one line of text; return next y."""
        row_img = render_text(text[:self._cols], color=color,
                              bg=bg, scale=self.font_scale)
        rh, rw = row_img.shape[:2]
        if y + rh > self.height:
            return y
        w = min(rw, self.width)
        fa = row_img[:, :w, 3:4].astype(np.float32) / 255.0
        for c in range(3):
            img[y:y+rh, :w, c] = (
                img[y:y+rh, :w, c] * (1.0 - fa[:, :, 0]) +
                row_img[:, :w, c]  * fa[:, :, 0]
            ).astype(np.uint8)
        img[y:y+rh, :w, 3] = 210
        return y + rh + 2

    def _draw_centered_list(self, img, title: str, items: List[str],
                            cursor: int,
                            base_colors: Optional[List] = None,
                            footer: str = '[Up/Dn]=nav [Esc]=back',
                            reserve_rows: int = 2):
        """
        Draw a scrollable list keeping the cursor row at vertical centre.

        base_colors  -- per-item fallback color; cursor always overrides to _SEL.
        reserve_rows -- rows reserved at the bottom (footer + status + extras).
                       Footer lands at height - ch*reserve_rows.
                       Status always lands at height - ch (last row).
        """
        ch = self._ch
        y  = self._blit_line(img, 0, title, _TITLE)

        if not items:
            self._blit_line(img, y, '[empty]', _WARN)
        else:
            n  = len(items)
            ci = min(cursor, n - 1)

            # Build display order: cursor in centre, expand symmetrically
            order = [ci]
            lo, hi = ci - 1, ci + 1
            while len(order) < n:
                added = False
                if lo >= 0:
                    order.insert(0, lo); lo -= 1; added = True
                if hi < n:
                    order.append(hi);  hi += 1; added = True
                if not added:
                    break

            cp     = order.index(ci)
            y_mid  = self.height // 2 - ch // 2
            bottom = self.height - ch * reserve_rows

            for pos, idx in enumerate(order):
                ry = y_mid + (pos - cp) * ch
                if ry < y or ry + ch > bottom:
                    continue
                sel    = (idx == ci)
                base   = base_colors[idx] if base_colors else _FG
                color  = _SEL if sel else base
                prefix = '>' if sel else ' '
                self._blit_line(img, ry, f'{prefix} {items[idx]}', color=color)

        fy = self.height - ch * reserve_rows
        self._blit_line(img, fy, footer, _DIM)
        if self._status and time.time() - self._status_ts < 4:
            self._blit_line(img, self.height - ch,
                            self._status[:self._cols], _TITLE)

    # --- sub-menu draw -------------------------------------------------

    def _draw_main(self, img):
        if self.menu_state.main_menu.is_active:
            self._draw_main_menu(img)
        elif self.menu_state.radio_menu.is_active:
            self._draw_radio_menu(img)
        elif self.menu_state.settings_menu.is_active:
            self._draw_settings_menu(img)
        elif self.menu_state.capabilities_menu.is_active:
            self._draw_capabilities_menu(img)
        elif self.menu_state.av_sources_menu.is_active:
            self._draw_av_sources_menu(img)

    def _draw_main_menu(self, img):
        self._draw_centered_list(
            img,
            title='=== ROBONET ===',
            items=_MAIN_ITEMS_BASE,
            cursor=self._cursor,
            footer='[Ent]=select  [Esc]=close',
        )

    def _draw_radio_menu(self, img):
        items = self._radio_items()
        # First 4 items are controls; endpoints follow
        colors = [_FG] * min(4, len(items)) + [_FG] * max(0, len(items) - 4)
        self._draw_centered_list(
            img,
            title='=== RADIO ===',
            items=items,
            cursor=self._cursor,
            base_colors=colors,
            footer='[Ent]=select  [Esc]=back',
        )

    def _draw_settings_menu(self, img):
        ch        = self._ch
        raw       = self._settings_items()
        labels    = [f'{k}: {v}' for k, v, _ in raw]
        colors    = [_FG if en else _DIM for _, _, en in raw]

        # When editing: reserve an extra row for the inline input box
        reserve = 3 if self._settings_edit else 2
        hint    = ('[Ent]=confirm  [Esc]=cancel'
                   if self._settings_edit
                   else '[Ent]=edit/toggle  [Esc]=back')

        self._draw_centered_list(
            img,
            title='=== SETTINGS ===',
            items=labels,
            cursor=self._cursor,
            base_colors=colors,
            footer=hint,
            reserve_rows=reserve,
        )

        if self._settings_edit:
            # Slot between footer (row -3) and status (row -1): row -2
            edit_y = self.height - ch * 2
            display = f'{self._edit_key}: {self._edit_buffer}|'
            self._blit_line(img, edit_y, display, _TITLE)

    def _draw_capabilities_menu(self, img):
        ch = self._ch

        # Tab bar: dim inactive pages, bright active
        tab_parts = []
        for i, name in enumerate(_CAPS_PAGES):
            tab_parts.append(f'[{name}]' if i == self._endpoint_page else f' {name} ')
        tab_line = '  '.join(tab_parts)

        items = self._capabilities_page_items()
        colors = [_KEY if self._endpoint_page == 0 else _FG] * len(items)

        self._draw_centered_list(
            img,
            title=f'=== CAPABILITIES: {tab_line} ===',
            items=items,
            cursor=self._cursor,
            base_colors=colors,
            footer='[<-/->]=page  [Up/Dn]=scroll  [Esc]=back',
        )

    def _draw_av_sources_menu(self, img):
        tab_parts = []
        for i, kind in enumerate(_AV_SOURCE_KINDS):
            label = _AV_SOURCE_KIND_LABELS[kind]
            tab_parts.append(f'[{label}]' if i == self._av_kind_index else f' {label} ')
        tab_line = '  '.join(tab_parts)

        items = self._av_source_items()

        self._draw_centered_list(
            img,
            title=f'=== AV SOURCES: {tab_line} ===',
            items=items,
            cursor=self._cursor,
            footer='[<-/->]=kind  [Up/Dn]=scroll  [Ent]=select  [Esc]=back',
        )

    def _draw_sudo(self, img):
        y = self._blit_line(img, 0, '=== SUDO ===', _WARN)
        for line in wrap_text(self._sudo_desc, self._cols):
            y = self._blit_line(img, y, line)
        y += self._ch
        y  = self._blit_line(img, y, 'Password:', _FG)
        y  = self._blit_line(img, y, '*' * len(self._password) or '_', _TITLE)
        y += self._ch
        self._blit_line(img, y, '[Ent]=ok  [Esc]=cancel', _DIM)