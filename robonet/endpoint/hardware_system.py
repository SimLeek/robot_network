from __future__ import annotations

import abc
import asyncio
import typing
from abc import ABC

import numpy as np

from robonet.buffers.buffer_objects import RobotCapabilities, MJpegCamFrame, SoundNpEvent, TensorBuffer, \
    SparseVectorBuffer
from robonet.gst_io.devices import get_first_camera_device, get_first_mic_device, DeviceNotFoundError, \
    get_first_speaker_device
from robonet.gst_io.receiver import GstReceiver
from robonet.gst_io.streamer import GstSender
from robonet.logging_setup import setup_logging

log = setup_logging()

import robonet.endpoint.settings as settings_

settings = settings_.get()

MIC_RMS_THRESHOLD = 0.002

if typing.TYPE_CHECKING:
    from robonet.endpoint.base import RobotNode


class RobotHardware(abc.ABC):
    def __init__(self):
        self.root = None

    def setup(self, parent: 'RobotNode'):
        self.root = parent

    # implement or override these

    def stop(self):  # stop software loops
        pass

    @abc.abstractmethod
    def build_capabilities(self) -> RobotCapabilities:
        ...

    @abc.abstractmethod
    def apply_tensor(self, idx_vec: np.ndarray, val_vec: np.ndarray):
        ...

    @abc.abstractmethod
    def halt(self):  # halt hardware
        ...

    @property
    def handlers(self) -> dict:
        return {
            "SparseVectorBuffer": self._on_spvec,
        }

    def async_loops(self) -> list:
        return []

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

    def update_server_ip(self, ip):
        pass

    def start(self):
        pass


class CamMicSpkRobotHardware(RobotHardware, ABC):
    """More specific general robot hardware that has a single camera, microphone, and speaker"""

    def __init__(self, camera=None, mic=None, speaker=None):
        super().__init__()

        if camera is None:
            try:
                camera = get_first_camera_device()
            except DeviceNotFoundError:
                log.error("Could not find a camera device. Will be starting without a camera.")
        if mic is None:
            try:
                mic = get_first_mic_device()
            except DeviceNotFoundError:
                log.error("Could not find a mic device. Will be starting without a mic.")
        if mic is None and camera is None:
            raise DeviceNotFoundError("No devices found")
        if speaker is None:
            try:
                speaker = get_first_speaker_device()
            except DeviceNotFoundError:
                log.error("Could not find a speaker device. Will be starting without a speaker.")

        with open(settings["psk_file"], "rb") as f:
            psk = f.read()

        self._gst_sender = GstSender(psk=psk,
                                     src_device=camera,
                                     mic_device=mic,
                                     sample_rate=48000,
                                     width=settings['cam_res'][0],  # make sure the camera actually has these
                                     height=settings['cam_res'][1],
                                     fps=settings['cam_fps'])
        self._gst_receiver = GstReceiver(psk=psk,
                                         recv_img_callback=None,
                                         direct_audio=True,  # <- gst will play received audio directly to speaker
                                         audio_output_device=speaker
                                         )
        self._server_ip = None

    @property
    def handlers(self) -> dict:
        # no collisions, just join
        our_handlers = super().handlers | self._gst_receiver.handlers | self._gst_sender.handlers
        return our_handlers

    def setup(self, parent: 'RobotNode'):
        super().setup(parent)
        self._gst_receiver.setup(parent)

    def update_server_ip(self, ip):
        self._server_ip = ip
        self._gst_sender.setup(self.root, ip)

    def start(self):
        # robot was selected. Don't start streams before then
        self._gst_sender.start()
        self._gst_receiver.start()

    def stop(self):
        self._gst_sender.stop()
        self._gst_receiver.stop()


