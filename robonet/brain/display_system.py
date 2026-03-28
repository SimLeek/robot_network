# in working state

import asyncio
import threading
import time
from typing import Tuple

import numpy as np
from displayarray import display

from robonet.brain.util.action_factory import ActionFactory
from robonet.brain.util.desktop_window_config import make_window_config_for_server
from robonet.brain.util.system_base import SubSystem
import robonet.brain.settings as settings_
settings = settings_.get()

import typing
if typing.TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem


class DisplaySubSystem(SubSystem):

    def __init__(self, out_res: Tuple[int, int] = None, fps=None):
        self.displayer = None   # displayarray display object; real window is self.displayer.window
        self.screen_lock = threading.Lock()
        if fps is None:
            fps = settings["ai_fps"]
        if out_res is None:
            out_res = settings["ai_res"]
        self.frame_time = 1.0 / fps
        self.out_res = out_res
        self.handlers = None
        self.in_img = np.zeros((self.out_res[1], self.out_res[0], 3), dtype=np.uint8)
        self.win_cfg = None
        self.af_thru = None
        self.af_edit = None

    def start(self):
        pass

    def stop(self):
        if self.displayer is not None:
            self.displayer.end()

    def setup(self, sm):
        self.af_thru = ActionFactory()
        self.af_edit = ActionFactory()
        self.win_cfg = make_window_config_for_server(sm, self.af_thru, self.af_edit)
        self.displayer = display(
            self.in_img, window_names=['screen'],
            mgl_config=self.win_cfg,
        )

    async def run_once(self, sm: 'ServerSystem'):
        t1 = time.time()
        img = self.in_img
        self.displayer.update(img, 'screen')
        elapsed = time.time() - t1
        await asyncio.sleep(max(0.0, self.frame_time - elapsed))

    async def run(self, sm: 'ServerSystem'):
        while not self.displayer.exited():
            await self.run_once(sm)

    def update_frame(self, img):
        self.in_img = img
