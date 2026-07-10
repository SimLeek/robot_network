from __future__ import annotations

import abc
import asyncio
import typing
from abc import ABC
from typing import Dict, Optional

import numpy as np

from robonet.buffers.buffer_objects import RobotCapabilities, MJpegCamFrame, SoundNpEvent, TensorBuffer, \
    SparseVectorBuffer, AVSourcesAnnounce, SelectAVSource, AVSourceError
from robonet.gst_io.devices import get_first_camera_device, get_first_mic_device, DeviceNotFoundError, \
    get_first_speaker_device
from robonet.gst_io.receiver_unencrypted import GstReceiver
from robonet.gst_io.streamer_unencrypted import GstSender
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

        self._gst_sender = GstSender(src_device=camera,
                                     mic_device=mic,
                                     sample_rate=48000,
                                     width=settings['cam_res'][0],  # make sure the camera actually has these
                                     height=settings['cam_res'][1],
                                     fps=settings['cam_fps'])
        self._gst_receiver = GstReceiver(recv_img_callback=None,
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


class MultiAVRobotHardware(RobotHardware, ABC):
    """General audio/video endpoint hardware: any number of selectable
    video sources, audio inputs, and audio outputs."""

    def __init__(self,
                video_sources: Dict[str, str] = None,
                audio_inputs: Dict[str, str] = None,
                audio_outputs: Dict[str, str] = None,
                active_video: Optional[str] = None,
                active_audio_in: Optional[str] = None,
                active_audio_out: Optional[str] = None,
                sample_rate: int = 48000):
        super().__init__()

        self._video_sources  = dict(video_sources or {})
        self._audio_inputs   = dict(audio_inputs or {})
        self._audio_outputs  = dict(audio_outputs or {})

        self._active_video     = active_video    if active_video    is not None else next(iter(self._video_sources), None)
        self._active_audio_in  = active_audio_in if active_audio_in is not None else next(iter(self._audio_inputs), None)
        self._active_audio_out = active_audio_out if active_audio_out is not None else next(iter(self._audio_outputs), None)

        video_device   = self._video_sources.get(self._active_video)
        mic_device     = self._audio_inputs.get(self._active_audio_in, 'default')
        speaker_device = self._audio_outputs.get(self._active_audio_out)

        self._gst_sender = GstSender(src_device=video_device,
                                     mic_device=mic_device,
                                     sample_rate=sample_rate,
                                     width=settings['cam_res'][0],
                                     height=settings['cam_res'][1],
                                     fps=settings['cam_fps'])
        self._gst_receiver = GstReceiver(recv_img_callback=None,
                                         direct_audio=True,
                                         audio_output_device=speaker_device)
        self._server_ip = None

    # -- capability announce / selection -----------------------------------

    def announce_sources(self) -> AVSourcesAnnounce:
        return AVSourcesAnnounce(
            video_ids=list(self._video_sources), audio_in_ids=list(self._audio_inputs),
            audio_out_ids=list(self._audio_outputs),
            active_video_id=self._active_video or '', active_audio_in_id=self._active_audio_in or '',
            active_audio_out_id=self._active_audio_out or '',
        )

    def _on_select_source(self, hostname: str, obj: SelectAVSource):
        sources = {'video': self._video_sources, 'audio_in': self._audio_inputs,
                  'audio_out': self._audio_outputs}.get(obj.kind)
        if sources is None:
            self._report_source_error(obj.kind, f'unknown kind {obj.kind!r}')
            return
        if obj.source_id not in sources:
            self._report_source_error(obj.kind, f'unknown source id {obj.source_id!r} for kind {obj.kind!r}')
            return

        method = {'video': self.select_video_source,
                 'audio_in': self.select_audio_input,
                 'audio_out': self.select_audio_output}[obj.kind]
        try:
            method(obj.source_id)
        except Exception as e:
            log.exception(f'select {obj.kind} -> {obj.source_id!r} failed')
            self._report_source_error(obj.kind, str(e))

    def _report_source_error(self, kind: str, message: str):
        reverted_to = {'video': self._active_video, 'audio_in': self._active_audio_in,
                      'audio_out': self._active_audio_out}.get(kind) or ''
        log.error(f'[av-source] {kind} switch failed: {message} -- staying on {reverted_to!r}')
        if self.root is not None:
            self.root.radio.burst(AVSourceError(kind=kind, message=message, reverted_to=reverted_to))

    def select_video_source(self, source_id: str):
        """Switch the active video source."""
        if source_id == self._active_video:
            return
        if source_id not in self._video_sources:
            raise KeyError(f'unknown video source id {source_id!r}')
        previous = self._active_video
        device = self._video_sources[source_id]
        try:
            self._gst_sender.set_source_device(device)
            self._active_video = source_id
        except Exception:
            if previous is not None:
                self._gst_sender.set_source_device(self._video_sources[previous])
            raise

    def select_audio_input(self, source_id: str):
        """Switch the active audio input (mic)"""
        if source_id == self._active_audio_in:
            return
        if source_id not in self._audio_inputs:
            raise KeyError(f'unknown audio input id {source_id!r}')
        previous = self._active_audio_in
        device = self._audio_inputs[source_id]
        try:
            self._gst_sender.set_mic_device(device)
            self._active_audio_in = source_id
        except Exception:
            if previous is not None:
                self._gst_sender.set_mic_device(self._audio_inputs[previous])
            raise

    def select_audio_output(self, source_id: str):
        """Switch the active audio output (speaker)."""
        if source_id == self._active_audio_out:
            return
        if source_id not in self._audio_outputs:
            raise KeyError(f'unknown audio output id {source_id!r}')
        previous = self._active_audio_out
        device = self._audio_outputs[source_id]
        try:
            self._gst_receiver.set_direct_audio(True, device)
            self._active_audio_out = source_id
        except Exception:
            if previous is not None:
                self._gst_receiver.set_direct_audio(True, self._audio_outputs[previous])
            raise

    # -- RobotHardware/lifecycle wiring --------------------------------------

    @property
    def handlers(self) -> dict:
        our_handlers = super().handlers | self._gst_receiver.handlers | self._gst_sender.handlers
        our_handlers['SelectAVSource'] = self._on_select_source
        return our_handlers

    def setup(self, parent: 'RobotNode'):
        super().setup(parent)
        self._gst_receiver.setup(parent)

    def update_server_ip(self, ip):
        self._server_ip = ip
        self._gst_sender.setup(self.root, ip)
        if self.root is not None:
            self.root.radio.burst(self.announce_sources())

    def start(self):
        self._gst_sender.start()
        self._gst_receiver.start()

    def stop(self):
        self._gst_sender.stop()
        self._gst_receiver.stop()


