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
        self._audio_leftover: np.ndarray = np.zeros(0, dtype=np.float32)
        self._audio_started_playing: bool = False

        self.win_cfg = None
        self.af_thru = None
        self.af_edit = None
        self._fullscreen_key_disabled = False

    def start(self):
        # play_audio previously existed in settings but was read by
        # nothing at all -- now it actually gates playback.
        if settings["play_audio"]:
            self._start_audio(self._audio_sample_rate)
            # pass # may remove if display glsl/gil blocks audio
        else:
            log.info('[display] play_audio=False -- received audio will be displayed but not played')

    def _resolve_speaker_device(self):
        """None = let sounddevice use its own implicit default. A
        substring of a device name (case-insensitive) or a device
        index both work directly, and take priority over auto-detect.
        Auto-detect prefers a device name suggesting real pulse/
        pipewire routing over sounddevice's own implicit default --
        on Linux that can land on the first raw ALSA hardware device
        instead of the one actually configured as the system output."""
        configured = settings["speaker_device"]
        if configured is not None:
            return configured
        try:
            devices = sd.query_devices()
        except Exception as e:
            log.warning(f'[display] could not query audio devices: {e}')
            return None
        for i, d in enumerate(devices):
            if d.get('max_output_channels', 0) > 0 and 'pulse' in d.get('name', '').lower():
                return i
        for i, d in enumerate(devices):
            if d.get('max_output_channels', 0) > 0 and 'pipewire' in d.get('name', '').lower():
                return i
        return None  # nothing preferred found -- fall back to sounddevice's own default

    def _start_audio(self, sample_rate: int = 48000):
        """Open a sounddevice OutputStream for the given sample rate.
        Called automatically on setup; call again if sample rate changes."""
        if self._audio_stream is not None:
            self._audio_stream.stop()
            self._audio_stream.close()
        self._audio_sample_rate = sample_rate
        self._audio_leftover = np.zeros(0, dtype=np.float32)
        self._audio_started_playing = False
        device = self._resolve_speaker_device()
        print(f"resolved device: {device}")
        self._audio_stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype='float32',
            callback=self._audio_cb,
            blocksize=0,  # let sounddevice pick a low-latency block size
            device=device,
            finished_callback=self._on_audio_stream_finished,
        )
        self._audio_stream.start()
        actual = sd.query_devices(self._audio_stream.device)
        log.info(f"[display] audio output stream started at {sample_rate} Hz on device "
                f"{self._audio_stream.device} ({actual.get('name', 'unknown')})")

    def _on_audio_stream_finished(self):
        # Fires whenever the stream stops for ANY reason -- including a
        # callback exception aborting it, which is otherwise completely
        # invisible (sounddevice only prints to stderr). Expected once
        # on normal shutdown; at any other time it means playback died.
        log.warning('[display] audio output stream stopped')

    def _audio_cb(self, outdata: np.ndarray, frames: int, time_info, status):
        # Runs on PortAudio's own thread.
        try:
            if status:
                log.warning(f'[display] audio output status: {status}')
            while True:
                try:
                    chunk = self._audio_queue.get_nowait()
                    self._audio_leftover = np.concatenate((self._audio_leftover, chunk))
                except queue.Empty:
                    break
            if not self._audio_started_playing:
                if len(self._audio_leftover) < 1024:
                    outdata[:] = 0
                    return
                else:
                    self._audio_started_playing = True
                    log.info('[display] first audio samples written to the output device')
            if len(self._audio_leftover) >= frames:
                outdata[:, 0] = self._audio_leftover[:frames]
                self._audio_leftover = self._audio_leftover[frames:]
            else:
                n = len(self._audio_leftover)
                log.warning(f'[display] Audio underrun! Missed {frames - n} frames.')
                if n > 0:
                    outdata[:n, 0] = self._audio_leftover
                outdata[n:] = 0
                # Force a re-buffer
                self._audio_leftover = np.array([], dtype='float32')
                self._audio_started_playing = False

        except Exception as e:
            log.error(f'[display] audio callback error (outputting silence): {e}')
            outdata[:] = 0

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
        if aud.dtype == np.int16:
            aud = aud.astype(np.float32) / 32768.0  # Normalize to [-1.0, 1.0]
        else:
            aud = np.asarray(aud, dtype=np.float32)

        self.in_aud = aud
        if aud is None:
            return
        # Flatten + float32 up front: the stream callback assigns into
        # a (frames, 1) float32 buffer, and a (N, 1)-shaped or float64
        # chunk sneaking in here would raise inside the callback --
        # which aborts the whole stream (see _audio_cb).
        chunk = np.asarray(aud, dtype=np.float32).reshape(-1)
        try:
            self._audio_queue.put_nowait(chunk)
        except queue.Full:
            # Stay realtime: drop the OLDEST chunk, keep the newest.
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(chunk)
            except queue.Full:
                log.debug('[display] audio queue full -- dropping chunk')
