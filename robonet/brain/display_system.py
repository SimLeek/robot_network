# in working state

import asyncio
import threading
import time
from typing import Tuple, Optional
import queue
import sounddevice as sd
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

def reshape_to_square_matrix(arr):
    n = arr.size
    for i in range(int(np.sqrt(n)), 0, -1):
        if n % i == 0:
            rows = i
            cols = n // i
            break
    
    return arr.reshape(rows, cols)

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
        self._audio_sample_rate: int = 48000
        self._audio_stream: Optional[sd.OutputStream] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=8)

        self.win_cfg = None
        self.af_thru = None
        self.af_edit = None

    def start(self):
        self._start_audio(self._audio_sample_rate)

    def _start_audio(self, sample_rate: int = 48000):
        """Open a sounddevice OutputStream for the given sample rate.
        Called automatically on setup; call again if sample rate changes."""
        if self._audio_stream is not None:
            self._audio_stream.stop()
            self._audio_stream.close()
        self._audio_sample_rate = sample_rate
        self._audio_stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype='float32',
            callback=self._audio_cb,
            blocksize=0,  # let sounddevice pick a low-latency block size
        )
        self._audio_stream.start()
        log.info(f'[display] audio output stream started at {sample_rate} Hz')

    def _audio_cb(self, outdata: np.ndarray, frames: int,
                  time_info, status):
        # status carries underrun/overflow flags from the driver
        if status:
            log.debug(f'[display] audio stream status: {status}')
        try:
            chunk = self._audio_queue.get_nowait()
        except queue.Empty:
            # No data ready -- output silence rather than blocking the audio thread
            outdata[:] = 0
            return
        # chunk may be shorter or longer than frames; fit it safely
        n = min(len(chunk), frames)
        outdata[:n, 0] = chunk[:n]
        if n < frames:
            outdata[n:] = 0

    def stop(self):
        if self._audio_stream is not None:
            self._audio_stream.stop()
            self._audio_stream.close()
            self._audio_stream = None
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
        aud = self.in_aud
        if aud is not None:
            if len(aud.shape)==1:
                aud = reshape_to_square_matrix(aud)
            self.displayer.update(aud, 'audio')
        elapsed = time.time() - t1
        await asyncio.sleep(max(0.0, self.frame_time - elapsed))

    async def run(self, sm: 'ServerSystem'):
        while not self.displayer.exited():
            await self.run_once(sm)

    def update_frame(self, img):
        self.in_img = img

    def update_audio(self, aud):
        self.in_aud = aud
        # assuming the audio received has the same sample_rate, chunk size, etc.
        try:
            self._audio_queue.put_nowait(aud)
        except queue.Full:
            log.debug('[display] audio queue full -- dropping chunk')
