"""
robonet/endpoint/desktop_hardware.py

Handles typical desktop hardware such as screens, mics, speakers, keyboards, and mice
"""

from __future__ import annotations

import logging

import numpy as np

from robonet.buffers.buffer_objects import KeyEvent, MouseEvent, RobotCapabilities
from robonet.desktop_control_spec import DESKTOP_CONTROL_INTERFACE_SPEC
from robonet.endpoint.desktop_capture import DesktopCaptureError
from robonet.endpoint.hardware_system import MultiAVRobotHardware
from robonet.endpoint.radio_system import HOSTNAME
from robonet.gst_io.devices import find_camera_devices, get_first_speaker_device, DeviceNotFoundError
from robonet.gst_io.streamer_unencrypted import VIDEO_SOURCE_XIMAGESRC, AUDIO_SOURCE_DESKTOP_MIX
import robonet.endpoint.settings as settings_

settings = settings_.get()

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

# pyglet mouse button bitmask values (confirmed via pyglet.window.mouse
# source: LEFT=1<<0, MIDDLE=1<<1, RIGHT=1<<2) -- NOT GLFW's sequential
# 0/1/2 indices. moderngl_window doesn't expose a normalized mouse-button
# abstraction the way it does for Keys, so this is pyglet-specific; a
# different backend would need revisiting, same as the keycode mapping.
_MOUSE_BUTTON_NAMES = {1: 'left', 4: 'right', 2: 'middle'}

DESKTOP_VIDEO_ID = 'desktop'
DESKTOP_AUDIO_IN_ID = 'desktop-audio'


def build_desktop_capabilities() -> RobotCapabilities:
    axes = [
        {'name': channel, 'description': info['hz'], 'keys': [], 'neuron': i}
        for i, (channel, info) in enumerate(DESKTOP_CONTROL_INTERFACE_SPEC.items())
    ]
    screen = pyautogui.size()  # actual screen resolution -- NOT settings['cam_res'],
                              # which is the transmitted/downscaled size and a
                              # genuinely different number the brain needs to keep
                              # separate for mouse-position scaling to land correctly.
    streams = [
        {'name': 'screen', 'type': 'video', 'io': 'I', 'width': screen.width, 'height': screen.height},
        {'name': 'mic', 'type': 'audio', 'io': 'I', 'sample_rate': 48000, 'channels': 1},
        {'name': 'speaker', 'type': 'audio', 'io': 'O', 'sample_rate': 48000, 'channels': 1},
    ]
    return RobotCapabilities.build(axes=axes, streams=streams, hostname=HOSTNAME, endpoint_type='desktop')


class DesktopHw(MultiAVRobotHardware):
    """Streams the screen and audio out, and replays received keyboard/mouse events via pyautogui.

    Captures directly (ximagesrc for video, pulsesrc for audio) rather
    than through a v4l2loopback/ALSA-loopback intermediary -- that
    round trip turned out to just produce blank output, and a direct
    capture is simpler and doesn't need any kernel modules loaded at
    all. Resolution/fps for the desktop video source are controlled the
    same way as for any other camera, via settings['cam_res']/['cam_fps']
    (GstSender's existing videoscale/capsfilter chain), not a separate
    capture-side setting.
    """

    def __init__(self, camera: str = None, speaker: str = None):
        if pyautogui is None:
            raise DesktopCaptureError(
                "pyautogui could not be imported (it needs a live, connectable "
                f"X DISPLAY): {_PYAUTOGUI_IMPORT_ERROR}. If this is running as a "
                "systemd --user service, make sure it's started with the DISPLAY "
                "environment variable set, e.g. Environment=DISPLAY=:0 in the "
                "service's [Service] section."
            )
        webcam_device = camera or settings['camera_device']
        if webcam_device is None:
            try:
                webcam_device = find_camera_devices()[0]
            except IndexError:
                log.error("Could not find a webcam device. Will be starting without one.")
        log.info(f"[hardware] using camera device: {webcam_device!r}")

        video_sources = {DESKTOP_VIDEO_ID: VIDEO_SOURCE_XIMAGESRC}
        if webcam_device:
            video_sources[f'webcam:{webcam_device}'] = webcam_device

        audio_inputs = {DESKTOP_AUDIO_IN_ID: AUDIO_SOURCE_DESKTOP_MIX}

        speaker_device = speaker or settings['speaker_device']
        if speaker_device is None:
            try:
                speaker_device = get_first_speaker_device()
            except DeviceNotFoundError:
                log.error("Could not find a speaker device. Will be starting without one.")
        log.info(f"[hardware] using speaker device: {speaker_device!r}")

        audio_outputs = {}
        if speaker_device:
            audio_outputs[f'speaker:{speaker_device}'] = speaker_device

        # Native screen resolution, not settings['cam_res'] -- desktop
        # content is mostly static and compresses well at full res, and
        # local (display-side) zoom/pan needs the actual detail to be
        # useful. cam_fps still applies; screen capture doesn't
        # meaningfully benefit from a resolution knob the way framerate
        # does for bandwidth.
        screen = pyautogui.size()

        super().__init__(video_sources=video_sources, audio_inputs=audio_inputs,
                         audio_outputs=audio_outputs, sample_rate=48000,
                         width=screen.width, height=screen.height)

        self._held_keys: set = set()
        self._held_buttons: set = set()

    def setup(self, parent):
        super().setup(parent)
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
        super().stop()

    def apply_tensor(self, idx_vec: np.ndarray, val_vec: np.ndarray):
        # The base class requires this method to exist.
        if len(idx_vec):
            log.debug(f'DesktopHw.apply_tensor called with {len(idx_vec)} entries -- ignored '
                     '(desktop control uses KeyEvent/MouseEvent, not SparseVectorBuffer)')
