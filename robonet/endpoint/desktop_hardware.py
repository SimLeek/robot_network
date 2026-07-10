"""
robonet/endpoint/desktop_hardware.py

Handles typical desktop hardware such as screens, mics, speakers, keyboards, and mice
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from robonet.buffers.buffer_objects import KeyEvent, MouseEvent, RobotCapabilities
from robonet.endpoint.desktop_capture import (
    DesktopVideoFeeder, DesktopAudioFeeder,
    ensure_v4l2loopback_device, ensure_alsa_loopback,
    DesktopCaptureError,
)
from robonet.endpoint.hardware_system import MultiAVRobotHardware
from robonet.endpoint.radio_system import HOSTNAME
from robonet.gst_io.devices import find_camera_devices, get_first_speaker_device, DeviceNotFoundError

log = logging.getLogger(__name__)

# pyautogui (via its mouseinfo submodule) hard-crashes on import in any
# process without a live, connectable DISPLAY -- e.g. a systemd --user service
# started before the X session is up, or some testing.
_PYAUTOGUI_IMPORT_ERROR = None
try:
    import pyautogui
    # pyautogui adds a 0.1s pause after every single call by default --
    # Too slow for interactive remote control.
    pyautogui.PAUSE = 0.0
    pyautogui.FAILSAFE = False
    # Most of the connected machines don't have another interface anyway
    # so moving the mouse to the corner to end pyautogui is a confusing glitch
    # and is nearly impossible to do anyway in practice when the mouse
    # is controlled by an active script
    # For a better failsafe: disconnect the wifi, eth, or if autostart
    # and localhost are set up, use a rescue disk to turn those off.
except Exception as _e:
    pyautogui = None
    _PYAUTOGUI_IMPORT_ERROR = _e

_MOUSE_BUTTON_NAMES = {0: 'left', 1: 'right', 2: 'middle'}

DESKTOP_VIDEO_ID = 'desktop'
DESKTOP_AUDIO_IN_ID = 'desktop-audio'


def build_desktop_capabilities() -> RobotCapabilities:
    return RobotCapabilities.build(axes=[], streams=[], hostname=HOSTNAME, endpoint_type='desktop')


class DesktopHw(MultiAVRobotHardware):
    """Streams the screen and audio out, and replays received keyboard/mouse events via pyautogui."""

    def __init__(self,
                capture_width: int = -1, capture_height: int = -1,
                capture_fps: int = 15, include_mic: bool = True):
        if pyautogui is None:
            raise DesktopCaptureError(
                "pyautogui could not be imported (it needs a live, connectable "
                f"X DISPLAY): {_PYAUTOGUI_IMPORT_ERROR}. If this is running as a "
                "systemd --user service, make sure it's started with the DISPLAY "
                "environment variable set, e.g. Environment=DISPLAY=:0 in the "
                "service's [Service] section."
            )
        loopback_video = ensure_v4l2loopback_device()
        loopback_audio_playback, loopback_audio_capture = ensure_alsa_loopback()
        try:
            webcam_device = find_camera_devices(exclude_devices=[loopback_video])[0] # todo: allow switching to any camera
        except IndexError:
            log.error("Could not find a webcam device. Will be starting without one.")
            webcam_device = None
        video_sources = {DESKTOP_VIDEO_ID: loopback_video}
        if webcam_device:
            video_sources[f'webcam:{webcam_device}'] = webcam_device

        audio_inputs = {DESKTOP_AUDIO_IN_ID: loopback_audio_capture}

        audio_outputs = {}
        try:
            speaker = get_first_speaker_device()
            audio_outputs[f'speaker:{speaker}'] = speaker
        except DeviceNotFoundError:
            log.error("Could not find a speaker device. Will be starting without one.")

        super().__init__(video_sources=video_sources, audio_inputs=audio_inputs,
                         audio_outputs=audio_outputs, sample_rate=48000)

        self._video_feeder = DesktopVideoFeeder(
            loopback_video, capture_width, capture_height, capture_fps)
        self._audio_feeder = DesktopAudioFeeder(
            loopback_audio_playback, sample_rate=48000, include_mic=include_mic)

        self._held_keys: set = set()
        self._held_buttons: set = set()

    def setup(self, parent):
        super().setup(parent)
        # Hardware always needs to be running, independent of whether a server ever connects
        self._video_feeder.start()
        self._audio_feeder.start()
        log.info(f'Desktop hardware started (video source: {self._active_video})')

    def build_capabilities(self) -> RobotCapabilities:
        return build_desktop_capabilities()

    @property
    def handlers(self) -> dict:
        return super().handlers | {
            'KeyEvent': self._on_key_event,
            'MouseEvent': self._on_mouse_event,
        }

    # -- input replay ------------------------------------------------------

    def _on_key_event(self, hostname: str, obj: KeyEvent):
        try:
            if obj.pressed:
                pyautogui.keyDown(obj.key)
                self._held_keys.add(obj.key)
            else:
                pyautogui.keyUp(obj.key)
                self._held_keys.discard(obj.key)
        except pyautogui.FailSafeException:
            log.warning('pyautogui failsafe triggered (mouse in corner) -- ignoring key event')
        except Exception as e:
            log.error(f'KeyEvent replay failed for key={obj.key!r}: {e}')

    def _on_mouse_event(self, hostname: str, obj: MouseEvent):
        button = _MOUSE_BUTTON_NAMES.get(obj.button, 'left')
        try:
            if obj.event_type == 0:      # move
                pyautogui.moveTo(obj.x, obj.y)
            elif obj.event_type == 1:    # press
                pyautogui.mouseDown(x=obj.x, y=obj.y, button=button)
                self._held_buttons.add(button)
            elif obj.event_type == 2:    # release
                pyautogui.mouseUp(x=obj.x, y=obj.y, button=button)
                self._held_buttons.discard(button)
            elif obj.event_type == 3:    # scroll
                pyautogui.scroll(obj.delta)
        except pyautogui.FailSafeException:
            log.warning('pyautogui failsafe triggered (mouse in corner) -- ignoring mouse event')
        except Exception as e:
            log.error(f'MouseEvent replay failed (type={obj.event_type}): {e}')

    # -- lifecycle -----------------------------------------------------------

    def _release_all_held(self):
        """Safety: don't leave keys/mouse buttons stuck down if the brain
        disconnects or the endpoint shuts down mid-press."""
        for key in list(self._held_keys):
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
        self._held_keys.clear()
        for button in list(self._held_buttons):
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                pass
        self._held_buttons.clear()

    def halt(self):
        self._release_all_held()

    def stop(self):
        self._release_all_held()
        self._video_feeder.stop()
        self._audio_feeder.stop()
        super().stop()

    def apply_tensor(self, idx_vec: np.ndarray, val_vec: np.ndarray):
        # The base class requires this method to exist.
        if len(idx_vec):
            log.debug(f'DesktopHw.apply_tensor called with {len(idx_vec)} entries -- ignored '
                     '(desktop control uses KeyEvent/MouseEvent, not SparseVectorBuffer)')
