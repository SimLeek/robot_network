# todo: needs testing

import asyncio
import threading
import time
from typing import Tuple, Dict

import cv2
import numpy as np

from robonet.brain.robot_system import RobotSubSystem
from robonet.brain.desktop_system import DesktopSubSystem
from robonet.brain.util.action_factory import ActionFactory
from robonet.brain.util.selection_menu import SelectionMenu, MenuVisState
from robonet.brain.util.system_base import SubSystem
from robonet.brain.util.viewport import Viewport
from robonet.buffers.buffer_objects import MJpegCamFrame, RobotStart
import robonet.brain.settings as settings_
from robonet.gst_io.devices import get_first_mic_device, DeviceNotFoundError
from robonet.gst_io.receiver_unencrypted import GstReceiver
from robonet.gst_io.streamer_unencrypted import GstSender
from robonet.logging_setup import setup_logging

log = setup_logging()
settings = settings_.get()

# 'unknown' deliberately not registered -- falls back to RobotSubSystem in _connect()
SubSystem.register('robot', RobotSubSystem)
SubSystem.register('desktop', DesktopSubSystem)

import typing
if typing.TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem
    from robonet.brain.util.network_scanner import Endpoint


class MenuSubSystem(SubSystem):
    """Selection menu composited over the display frame.

    Displays endpoints discovered by RadioSubSystem.

    Toggle with Ctrl+backtick (human) or token/neuron 800/300 (AI).
    """
    NO_ENDPOINTS_TIMEOUT = 30.0   # fallback defaults if settings is somehow unavailable
    IDLE_TIMEOUT = 600.0

    def __init__(self, out_res: Tuple[int, int] = None, fps:float=None):
        # settings-driven defaults; still fine to set explicitly afterwards,
        # e.g. for AI runs, same as before.
        self.does_timeout = settings['auto_shutdown_enabled']
        self.no_endpoints_timeout = settings['auto_shutdown_no_endpoints_timeout'] or self.NO_ENDPOINTS_TIMEOUT
        self.idle_timeout = settings['auto_shutdown_idle_timeout'] or self.IDLE_TIMEOUT
        if out_res is None:
            out_res = settings["ai_res"]
        if fps is None:
            fps = settings["ai_fps"]
        self.out_res = out_res
        self.fps = fps
        self._menu = SelectionMenu(width=out_res[0], height=out_res[1])
        self._connected  = False
        self._start_time = 0.0
        self.is_running = True
        self.root: 'ServerSystem' = None
        self.screen_lock = threading.Lock()
        self.last_img = np.zeros((self.out_res[1], self.out_res[0], 3), dtype=np.uint8)
        self.last_audio = None
        self._logged_first_img = False
        self._logged_first_audio = False
        # Aspect-preserving scale for non-desktop endpoints (e.g. a
        # robot) -- always baseline zoom, no interactive zoom/pan.
        # Desktop endpoints use DesktopSubSystem's own viewport instead,
        # since zoom/pan is desktop-specific (large/multiple monitors);
        # robots have a movable camera rather than a fixed viewport.
        self._fallback_viewport = Viewport()
        self.handlers = None

        # todo: move this all somewhere other than menu
        mic = None
        try:
            mic = get_first_mic_device()
        except DeviceNotFoundError:
            log.error("Could not find a mic device. Will be starting without a mic.")

        with open(settings["psk_file"], "rb") as f:
            psk = f.read()

        self._gst_sender = GstSender(src_device=None,
                                     mic_device=mic,
                                     sample_rate=48000)
        self._gst_receiver = GstReceiver(recv_img_callback=self.on_img,
                                         recv_audio_callback=self.on_audio
                                         #direct_audio=False,  # <- gst will play received audio directly to speaker
                                         #audio_output_device=speaker
                                         )

    def set_endpoints(self, eps):
        self._menu.set_endpoints(eps)

    @property
    def gst_sender(self):
        """The active GstSender"""
        return self._gst_sender

    @property
    def gst_receiver(self):
        """The active GstReceiver"""
        return self._gst_receiver

    def setup(self, root: 'ServerSystem'):
        self.root = root
        self._menu.root = root
        self.handlers = (self._gst_receiver.handlers | self._gst_sender.handlers | {
            'AVSourcesAnnounce': self._on_av_sources,
            'AVSourceError': self._on_av_source_error,
        })

    def _on_av_sources(self, hostname: str, obj):
        self._menu.set_av_sources(obj)

    def _on_av_source_error(self, hostname: str, obj):
        self._menu.set_status(
            f'{obj.kind} source error: {obj.message} (using {obj.reverted_to or "none"})')

    def start(self):
        self._start_time = time.time()

    def stop(self):
        self.is_running = False
        self._gst_receiver.stop()
        self._gst_sender.stop()

    def register_default_human_controls(self, af: ActionFactory):
        print('regdef')
        af.bind_keyboard(self.handle_keyboard)

    def register_edit_human_controls(self, af: ActionFactory):
        print('regedit')
        af.bind_keyboard(self.handle_keyboard)

    def handle_keyboard(self, key, action, modifiers):
        # if this is ever called, then self.root.displayer is not None
        keys = self.root.displayer.displayer.displayer.config.wnd.keys
        cfg = self.root.displayer.displayer.displayer.config
        # Ctrl+backtick: toggle menu in any mode
        if key == ord('`') and action == keys.ACTION_PRESS and modifiers.ctrl:
            self.toggle()
            return
        # Ctrl+Shift+2: toggle edit mode (zoom/pan the local view -- does
        # not touch the remote endpoint at all).
        if key == ord('2') and action == keys.ACTION_PRESS and modifiers.ctrl and modifiers.shift:
            cfg.input_mode = 1 if cfg.input_mode == 2 else 2
            if cfg.input_mode != 2 and isinstance(self.root.active_sub, DesktopSubSystem):
                self.root.active_sub._edit_mouse_pos = None  # stop edge-panning once we've left edit mode
            return
        # When menu is visible, route navigation keys to it; don't forward to remote
        if self.visible and action == keys.ACTION_PRESS:
            # displayarray can have varying backends. This isolates breaking changes.
            nav_map = {
                keys.UP:        'up',
                keys.DOWN:      'down',
                keys.LEFT:      'left',
                keys.RIGHT:     'right',
                keys.ENTER:     'enter',
                keys.TAB:       'escape',  # escape closes the window
                keys.BACKSPACE: 'backspace',
            }
            mapped = nav_map.get(key)
            if mapped:
                ep = self._menu.on_key(mapped)
                if ep is not None:
                    self._connect(ep)
            if 32 <= key <= 126:
                self._menu.on_char(chr(key))

    @property
    def visible(self) -> bool:
        return self._menu.visible

    def toggle(self):
        if self.root.active_sub:
            self._menu.toggle()
        else:
            self._menu.state = MenuVisState.MAIN

    def composite(self, frame: np.ndarray) -> np.ndarray:
        return self._menu.composite(frame)

    def _connect(self, ep: 'Endpoint'):
        """Connect to ep: wire up radio and swap in the right SubSystem."""
        sm = self.root
        if ep.ip is None:
            self._menu.set_status(f'Could not connect to {ep.hostname or ep.ip} [{ep.endpoint_type}], no ip.')
            return
        sm.radio.connect_to(ep.ip)

        self._connected = True
        self._menu.set_status(f'Connecting -> {ep.hostname or ep.ip} [{ep.endpoint_type}]')
        self._menu.toggle()

        # Stop previous sub if any
        if sm.active_sub:
            sm.active_sub.stop()
            # todo: put this somewhere else too
            self._gst_sender.stop()
            self._gst_receiver.stop()

        sm.radio.burst(RobotStart(hostname=ep.hostname, endpoint_type=ep.endpoint_type))

        # todo: put this somewhere else too
        self._gst_sender.setup(self.root, ep.ip)
        self._gst_receiver.setup(self.root)
        self._gst_sender.start()
        self._gst_receiver.start()

        try:
            sub_cls = SubSystem.for_endpoint_type(ep.endpoint_type)
        except KeyError:
            sub_cls = RobotSubSystem  # unknown endpoint_type
        new_sub = sub_cls(endpoint=ep)
        sm.swap_subsystem(new_sub)
        self._menu.clear_av_sources()

        if getattr(ep, 'axes', None) or getattr(ep, 'streams', None):
            self._menu.set_endpoint_capabilities(
                {'axes': ep.axes, 'streams': ep.streams})
        self._menu.set_status(f'Connected to {ep.hostname or ep.ip} [{ep.endpoint_type}]')

    def set_status(self, msg: str):
        self._menu.set_status(msg)

    def register_thru_token_controls(self, af: ActionFactory):
        af.bind_ai_token(self.toggle, 800)

    def register_thru_neuron_controls(self, af: ActionFactory):
        af.bind_ai_neuron(lambda v: self.toggle(), 300, 0.5)

    async def timeout_loop(self):
        """While does_timeout is on: shut down fast (no_endpoints_timeout,
         default 30s) if no endpoint has ever been seen.
        Once anything has been seen, switch to the much longer
         idle_timeout (default 10min) for the rest of the run if we later
         go idle: an AI or human could still be working through the menu."""
        ever_seen_anything = False
        last_active = time.time()   # 'active' = connected, or an endpoint currently visible
        while self.does_timeout:
            await asyncio.sleep(1.0)
            have_endpoints = bool(self._menu.get_unique_endpoints())
            if self._connected or have_endpoints:
                last_active = time.time()
                ever_seen_anything = True
                if not self._connected:
                    self._menu.set_status('Endpoint found - press Enter to connect')
                continue
            idle = time.time() - last_active
            timeout = self.idle_timeout if ever_seen_anything else self.no_endpoints_timeout
            remaining = int(timeout - idle)
            if remaining <= 0:
                self.root.shutdown(reason=f'no endpoints available for {timeout:.0f}s')
                return
            self._menu.set_status(f'No endpoints - shutdown in {remaining}s')

    async def send_frames_always(self):
        while self.is_running:
            t0 = time.time()
            img = self.last_img
            if img is not None:
                source_h, source_w = img.shape[:2]
                display_w, display_h = self.out_res
                active = self.root.active_sub
                if isinstance(active, DesktopSubSystem):
                    active.check_edge_pan(source_w, source_h, display_w, display_h, 1.0 / self.fps)
                    img = active.viewport.apply(img, display_w, display_h)
                else:
                    img = self._fallback_viewport.apply(img, display_w, display_h)
            img = self._menu.composite(img)
            if self.root.displayer is not None:
                self.root.displayer.update_frame(img)
                if self.last_audio is not None:
                    self.root.displayer.update_audio(self.last_audio)
            if self.root.ai is not None:
                self.root.ai.update_frame(img)
            t1 = time.time()
            t_remain = max(0.0, 1.0/self.fps - (t1-t0))
            await asyncio.sleep(t_remain)

    def async_loops(self, *args):
        return [self.timeout_loop(), self.send_frames_always()]

    def on_img(self, img:np.ndarray):
        #This is put in the menu since neither AI nor humans should be menu-less
        if not self._logged_first_img:
            log.info("gst img received (first frame -- further frames logged at DEBUG only)")
            self._logged_first_img = True
        else:
            log.debug("gst img received")
        with self.screen_lock:
            if img is None:
                log.error("Received None image.")
            else:
                self.last_img = img

    def on_audio(self, aud:np.ndarray):
        if not self._logged_first_audio:
            log.info("gst audio received (first chunk -- further chunks logged at DEBUG only)")
            self._logged_first_audio = True
        else:
            log.debug("gst audio received")
        self.last_audio = aud

