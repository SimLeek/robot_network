"""
robonet/endpoint/desktop_hardware.py

DesktopHw: the 'desktop' analog of MasterPiHw
(examples/basicpibot/robot_endpoint.py). Instead of driving robot motors,
it feeds this machine's own screen/audio out as a camera/mic stream (via
desktop_capture.py's feeders and a v4l2loopback + ALSA-loopback pair) and
replays keyboard/mouse events received from the brain via pyautogui, so
the brain can remote-control this machine.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from robonet.buffers.buffer_objects import (
    KeyEvent, MouseEvent, SwitchVideoSource, RobotCapabilities,
)
from robonet.endpoint.desktop_capture import (
    DesktopVideoFeeder, DesktopAudioFeeder,
    ensure_v4l2loopback_device, ensure_alsa_loopback, find_real_webcam,
    DesktopCaptureError,
)
from robonet.endpoint.hardware_system import CamMicSpkRobotHardware
from robonet.endpoint.radio_system import HOSTNAME
import robonet.endpoint.settings as settings_

settings = settings_.get()
log = logging.getLogger(__name__)

# pyautogui (via its mouseinfo submodule) connects to X at import time, so
# it hard-crashes on import in any process without a live, connectable
# DISPLAY -- e.g. a systemd --user service started before the X session is
# up, or this module simply being imported for testing. Caught like this,
# the module itself stays importable either way; DesktopHw.__init__ raises
# a clear DesktopCaptureError instead of a cryptic Xlib traceback if it's
# actually needed and unavailable.
try:
    import pyautogui
    # pyautogui adds a 0.1s pause after every single call by default --
    # much too slow for interactive remote control. We're already
    # rate-limited by incoming network events, not a tight loop, so this
    # is safe to zero.
    pyautogui.PAUSE = 0.0
    # FAILSAFE stays on (pyautogui default): dragging the physical mouse
    # into a screen corner is a free physical kill-switch for whoever is
    # sitting at this machine. We catch the exception it raises below
    # rather than letting it crash the endpoint process.
except Exception as _e:
    pyautogui = None
    _PYAUTOGUI_IMPORT_ERROR = _e

_MOUSE_BUTTON_NAMES = {0: 'left', 1: 'right', 2: 'middle'}


def build_desktop_capabilities() -> RobotCapabilities:
    """Desktop endpoints don't use the axis/key-binding control model
    robot endpoints use -- input is raw pass-through (KeyEvent/MouseEvent),
    so axes stays empty. Streams are still auto-populated the normal way
    once GstStreamInfo goes out, so nothing needed here either."""
    return RobotCapabilities.build(axes=[], streams=[], hostname=HOSTNAME, endpoint_type='desktop')


class DesktopHw(CamMicSpkRobotHardware):
    """Endpoint hardware for 'desktop mode': streams the screen and audio
    out, and replays received keyboard/mouse events via pyautogui."""

    def __init__(self,
                 capture_width: int = 1920, capture_height: int = 1080,
                 capture_fps: int = 30, include_mic: bool = True):
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

        # CamMicSpkRobotHardware.__init__ builds its GstSender from
        # settings['cam_res']/['cam_fps'], not from constructor args -- so
        # those need to reflect the desktop capture size *before* we call
        # super().__init__(), or the sender would downscale to the 640x480
        # webcam default and desktop text would be illegible. This is an
        # in-memory override only (no .save()), scoped to this process.
        settings['cam_res'] = [capture_width, capture_height]
        settings['cam_fps'] = capture_fps

        # camera=our virtual desktop cam, mic=the loopback CAPTURE side
        # (what the desktop is saying/playing), speaker=None so the base
        # class auto-detects a real speaker for normal incoming playback.
        super().__init__(camera=loopback_video, mic=loopback_audio_capture, speaker=None)

        self._loopback_video = loopback_video
        self._webcam_device  = find_real_webcam(exclude_device=loopback_video)
        self._current_source = 'desktop'

        self._video_feeder = DesktopVideoFeeder(
            loopback_video, capture_width, capture_height, capture_fps)
        self._audio_feeder = DesktopAudioFeeder(
            loopback_audio_playback, sample_rate=48000, include_mic=include_mic)

        self._held_keys: set = set()
        self._held_buttons: set = set()

    def setup(self, parent):
        super().setup(parent)
        # Hardware always needs to be running, independent of whether a
        # server ever connects -- matches MasterPiHw's philosophy, and
        # matches "keep running in the background" from how this is meant
        # to be used.
        self._video_feeder.start()
        self._audio_feeder.start()
        log.info(f'Desktop hardware started (video source: {self._current_source})')

    def build_capabilities(self) -> RobotCapabilities:
        return build_desktop_capabilities()

    @property
    def handlers(self) -> dict:
        return super().handlers | {
            'KeyEvent': self._on_key_event,
            'MouseEvent': self._on_mouse_event,
            'SwitchVideoSource': self._on_switch_video_source,
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

    def _on_switch_video_source(self, hostname: str, obj: SwitchVideoSource):
        if obj.source == self._current_source:
            return
        if obj.source == 'camera':
            if self._webcam_device is None:
                log.warning('SwitchVideoSource(camera) requested but no physical webcam was found')
                return
            self._gst_sender.set_source_device(self._webcam_device)
            self._current_source = 'camera'
        else:
            self._gst_sender.set_source_device(self._loopback_video)
            self._current_source = 'desktop'
        log.info(f'video source switched -> {self._current_source}')

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
        # Desktop endpoints are controlled via raw KeyEvent/MouseEvent, not
        # the sparse axis-tensor model robot endpoints use. Nothing to do,
        # but the base class requires this method to exist.
        if len(idx_vec):
            log.debug(f'DesktopHw.apply_tensor called with {len(idx_vec)} entries -- ignored '
                     '(desktop control uses KeyEvent/MouseEvent, not SparseVectorBuffer)')
