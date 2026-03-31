# todo: needs testing

import asyncio
import threading
import time
from typing import Tuple, Dict

import cv2
import numpy as np

from robonet.brain.robot_system import RobotSubSystem
from robonet.brain.util.action_factory import ActionFactory
from robonet.brain.util.selection_menu import SelectionMenu, MenuVisState
from robonet.brain.util.system_base import SubSystem
from robonet.buffers.buffer_objects import MJpegCamFrame, RobotStart
import robonet.brain.settings as settings_
from robonet.logging_setup import setup_logging

log = setup_logging()
settings = settings_.get()

import typing
if typing.TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem
    from robonet.brain.util.network_scanner import Endpoint


class MenuSubSystem(SubSystem):
    """Selection menu composited over the display frame.

    Displays endpoints discovered by RadioSubSystem.

    Toggle with Ctrl+backtick (human) or token/neuron 800/300 (AI).
    """
    SHUTDOWN_TIMEOUT = 30.0

    def __init__(self, out_res: Tuple[int, int] = None, fps:float=None):
        self.does_timeout = False  # set to true for AI runs
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
        self.handlers = None

    def set_endpoints(self, eps):
        self._menu.set_endpoints(eps)

    def setup(self, root: 'ServerSystem'):
        self.root = root
        self._menu.root = root
        #root.radio.on_endpoint_found = self._on_endpoint_found
        #root.radio.on_endpoint_lost = self._on_endpoint_lost
        self.handlers = {
            'MJpegCamFrame': self.mjpeg_handler(root)
        }

    def start(self):
        self._start_time = time.time()

    def stop(self):
        self.is_running = False

    def register_default_human_controls(self, af: ActionFactory):
        print('regdef')
        af.bind_keyboard(self.handle_keyboard)

    def register_edit_human_controls(self, af: ActionFactory):
        print('regedit')
        af.bind_keyboard(self.handle_keyboard)

    def handle_keyboard(self, key, action, modifiers):
        # if this is ever called, then self.root.displayer is not None
        keys = self.root.displayer.displayer.displayer.config.wnd.keys
        # Ctrl+backtick: toggle menu in any mode
        if key == ord('`') and action == keys.ACTION_PRESS and modifiers.ctrl:
            self.toggle()
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
        self._menu.set_status(f'Connecting → {ep.hostname or ep.ip} [{ep.endpoint_type}]')
        self._menu.toggle()

        # Stop previous sub if any
        if sm.active_sub:
            sm.active_sub.stop()

        sm.radio.burst(RobotStart(hostname=ep.hostname, endpoint_type=ep.endpoint_type))

        new_sub = RobotSubSystem(endpoint=ep)
        sm.swap_subsystem(new_sub)

        if getattr(ep, 'axes', None) or getattr(ep, 'streams', None):
            self._menu.set_robot_capabilities(
                {'axes': ep.axes, 'streams': ep.streams})
        self._menu.set_status(f'Connected to {ep.hostname or ep.ip} [{ep.endpoint_type}]')

    def register_thru_token_controls(self, af: ActionFactory):
        af.bind_ai_token(self.toggle, 800)

    def register_thru_neuron_controls(self, af: ActionFactory):
        af.bind_ai_neuron(lambda v: self.toggle(), 300, 0.5)

    async def timeout_loop(self):
        while self.does_timeout:
            await asyncio.sleep(1.0)
            if self._connected:
                return
            elapsed   = time.time() - self._start_time
            remaining = int(self.SHUTDOWN_TIMEOUT - elapsed)
            if not self._endpoints:
                if remaining <= 0:
                    raise RuntimeError('no endpoints found within timeout')
                self._menu.set_status(f'Scanning… shutdown in {remaining}s')
            else:
                self._menu.set_status('Endpoint found — press Enter to connect')

    async def send_frames_always(self):
        while self.is_running:
            t0 = time.time()
            #with self.screen_lock:
            img = self._menu.composite(self.last_img)
            if self.root.displayer is not None:
                self.root.displayer.update_frame(img)
            if self.root.ai is not None:
                self.root.ai.update_frame(img)
            t1 = time.time()
            t_remain = max(0.0, 1.0/self.fps - (t1-t0))
            await asyncio.sleep(t_remain)

    def async_loops(self, *args):
        return [self.timeout_loop(), self.send_frames_always()]

    def mjpeg_handler(self, sm: 'ServerSystem'):
        #This is put in the menu since neither AI nor humans should be menu-less
        def handler(hostname: str, obj: MJpegCamFrame):
            log.info(f"topic:{hostname}, obj:{type(obj)}")
            with self.screen_lock:
                if obj.encoding == 'mjpeg':
                    img = cv2.imdecode(np.frombuffer(obj.mjpeg, np.uint8), cv2.IMREAD_COLOR)
                else:
                    img = np.frombuffer(obj.mjpeg, dtype=np.uint8).reshape((obj.h, obj.w, 3))
                if obj.w>settings['ai_res'][0] or obj.h>settings['ai_res'][1]:
                    resized_image = cv2.resize(image, settings['ai_res'], interpolation=cv2.INTER_NEAREST)
                if img is None:
                    log.error("Received bad image. Could not decode.")
                else:
                    self.last_img = img

        return handler
