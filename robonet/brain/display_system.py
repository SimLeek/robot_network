# in working state

import asyncio
import threading
import time
from typing import Tuple, Optional
import numpy as np
from displayarray import display

from robonet.brain.util.action_factory import ActionFactory
from robonet.brain.util.desktop_window_config import make_window_config_for_server
from robonet.brain.util.system_base import SubSystem
import robonet.brain.settings as settings_
settings = settings_.get()
from robonet.logging_setup import setup_logging

log = setup_logging()
import typing
if typing.TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem

import numpy as np


def reshape_to_square_image(arr: np.ndarray, pad_to_rgb: bool = True) -> np.ndarray:
    n = arr.shape[0]
    for i in range(int(np.sqrt(n)), 0, -1):
        if n % i == 0:
            rows = i
            cols = n // i
            break

    reshaped = arr.reshape((rows, cols) + arr.shape[1:])

    if pad_to_rgb and reshaped.ndim >= 3 > reshaped.shape[-1]:
        pad_width = [(0, 0)] * reshaped.ndim
        pad_width[-1] = (0, 3 - reshaped.shape[-1])
        reshaped = np.pad(reshaped, pad_width, mode='constant', constant_values=0)

    return reshaped

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
        self.in_aud = None

        self.win_cfg = None
        self.af_thru = None
        self.af_edit = None
        self._fullscreen_key_disabled = False

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

    def _disable_builtin_fullscreen_key(self):
        """moderngl_window's own base Window class binds F11 to toggle
        fullscreen by default, before the keypress ever reaches
        pass_through_cb -- toggling fullscreen mid-keypress disrupts
        forwarding that same press to the endpoint. fullscreen_key=None
        is moderngl_window's own documented way to disable this."""
        if self._fullscreen_key_disabled:
            return
        try:
            wnd = self.displayer.displayer.config.wnd
        except AttributeError:
            return  # window not constructed yet -- retry next frame
        wnd.fullscreen_key = None
        self._fullscreen_key_disabled = True

    async def run_once(self, sm: 'ServerSystem'):
        self._disable_builtin_fullscreen_key()
        t1 = time.time()
        self.displayer.update(self.in_img, 'screen')
        aud = self.in_aud
        if aud is not None:
            # displayarray multiplies float input by 255 assuming it's
            # already 0..1 -- raw PCM samples are roughly -1..1, so the
            # negative half of every waveform was wrapping around via
            # uint8 underflow instead of displaying. Remap -1..1 -> 0..1.
            aud = (aud / 2.0) + 0.5
            if len(aud.shape) == 1:
                aud = reshape_to_square_matrix(aud)
            elif aud.shape[1] >= 2:
                aud = reshape_stereo_to_square_image(aud)
            self.displayer.update(aud, 'audio')
        elapsed = time.time() - t1
        await asyncio.sleep(max(0.0, self.frame_time - elapsed))

    async def run(self, sm: 'ServerSystem'):
        while not self.displayer.exited():
            await self.run_once(sm)

    def update_frame(self, img):
        self.in_img = img

    def update_audio(self, aud):
        if aud is None:
            self.in_aud = None
            return
        # The receive path already converts to float32 [-1, 1], but
        # accept raw int16 defensively too.
        if getattr(aud, 'dtype', None) == np.int16:
            aud = aud.astype(np.float32) / 32768.0
        else:
            aud = np.asarray(aud, dtype=np.float32)
        self.in_aud = aud
