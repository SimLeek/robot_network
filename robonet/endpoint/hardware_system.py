from __future__ import annotations

import abc
import asyncio
import typing

import numpy as np

from robonet.buffers.buffer_objects import RobotCapabilities, MJpegCamFrame, SoundNpEvent, TensorBuffer, \
    SparseVectorBuffer
from robonet.logging_setup import setup_logging
log = setup_logging()

MIC_RMS_THRESHOLD = 0.002

if typing.TYPE_CHECKING:
    from robonet.endpoint.base import RobotNode

class RobotHardware(abc.ABC):
    def __init__(self):
        self.root = None
        self._frame_queue = None
        self._audio_queue = None

    def setup( self, parent: 'RobotNode'):
        self.root = parent
        self._frame_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    # call these from the class that inherits this one
    def enqueue_frame(self, jpeg: bytes):
        if self.root.radio.is_streaming:
            self.root.loop.call_soon_threadsafe(self._drop_put, self._frame_queue, jpeg)

    def enqueue_audio(self, chunk: np.ndarray):
        if not self.root.radio.is_streaming:
            return
        if float(np.sqrt(np.mean(chunk**2))) >= MIC_RMS_THRESHOLD:
            self.root.loop.call_soon_threadsafe(
                self._drop_put, self._audio_queue, chunk.copy()
            )

    # implement or override these

    @abc.abstractmethod
    def build_capabilities(self) -> RobotCapabilities: ...

    @abc.abstractmethod
    def apply_tensor(self, idx_vec: np.ndarray, val_vec: np.ndarray): ...

    def apply_speaker(self, chunk: np.ndarray):
        raise NotImplementedError

    def mic_rate(self) -> int:
        return 48000

    @abc.abstractmethod
    async def start_streams(self): ...

    @abc.abstractmethod
    async def stop_streams(self): ...

    @abc.abstractmethod
    def halt(self): ...

    @property
    def handlers(self) -> dict:
        return {
            "SparseVectorBuffer": self._on_spvec,
            "SoundNpEvent": self._on_sound,
        }

    def async_loops(self) -> list:
        return [self._transmit_camera_loop(), self._transmit_audio_loop()]

    @staticmethod
    def _drop_put(q: asyncio.Queue, item):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _transmit_camera_loop(self):
        while True:
            jpeg = await self._frame_queue.get()
            self.root.radio.burst(MJpegCamFrame(brightness=0, exposure=0, mjpeg=jpeg))

    async def _transmit_audio_loop(self):
        rate = self.mic_rate()
        while True:
            chunk = await self._audio_queue.get()
            self.root.radio.burst(SoundNpEvent(arrays=[chunk], sample_rate=rate))

    def _on_spvec(self, hostname: str, obj: SparseVectorBuffer):
        self.root.radio.update_last_ctrl()
        idx = obj.idx.flatten().astype(np.uint32)
        vec = obj.val.flatten().astype(np.float32)
        if len(vec) == 0:
            return  # watchdog ping
        try:
            self.apply_tensor(idx, vec)
        except Exception as e:
            log.exception(f"apply_tensor failed: {e}")

    def _on_sound(self, hostname: str, obj: SoundNpEvent):
        self.root.radio.update_last_ctrl()
        if not obj.arrays:
            return
        chunk = (
            np.mean(np.stack(obj.arrays), axis=0)
            if len(obj.arrays) > 1
            else obj.arrays[0]
        )
        try:
            self.apply_speaker(chunk.astype(np.float32))
        except Exception as e:
            log.exception(f"apply_speaker failed: {e}")


