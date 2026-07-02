"""
robonet/endpoint/desktop_capture.py

Turns "the X11 desktop and its audio" into a normal-looking webcam + mic,
so the existing v4l2src/alsasrc based GstSender (streamer_unencrypted.py)
can send them without any changes to that pipeline code.

How it works
------------
Video: a v4l2loopback virtual camera device is created ahead of time by
       examples/desktop/setup_desktop_capture.sh (one-time, needs sudo).
       DesktopVideoFeeder runs "ximagesrc ! ... ! v4l2sink" continuously,
       writing the live desktop into that virtual camera. GstSender then
       reads from the virtual camera exactly like it would a webcam.

Audio: an ALSA snd-aloop pair is created the same way. Loopback devices
       come in matched pairs: whatever is played into the "0,0" side can
       be captured back out of the "1,0" side. DesktopAudioFeeder mixes
       the desktop's system audio (the default sink's monitor) with the
       machine's real microphone (if present) and plays that mix into
       the "0,0" side. GstSender then captures from "1,0" exactly like
       it would a real microphone.

This module only finds/feeds the devices. It does not create the kernel
modules or the v4l2loopback device itself -- that needs root once, and is
handled by the setup script, not silently from here.

Assumes X11 (ximagesrc) and a PulseAudio-compatible sound server (plain
PulseAudio, or PipeWire's pipewire-pulse, which is the Arch Linux default
as of this writing). On Wayland, ximagesrc will not see anything; swap it
for pipewiresrc once portal permission plumbing is worked out. On a
system with no PulseAudio-compatible socket, the audio feeder logs a
warning and is skipped -- video-only desktop capture still works.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import threading
from typing import Optional, Tuple

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gst, GLib

Gst.init(None)

from robonet.logging_setup import setup_logging

log = setup_logging()

DESKTOP_CAM_LABEL = 'RobonetDesktopCam'


class DesktopCaptureError(Exception):
    """Raised when a desktop-capture prerequisite (kernel module, device)
    is missing. Not raised for things that can degrade gracefully, such
    as no audio server being present.
    """
    pass


# ---------------------------------------------------------------------------
# Device discovery -- never modprobes or creates devices itself.
# See examples/desktop/setup_desktop_capture.sh for the one-time root setup.
# ---------------------------------------------------------------------------

def find_v4l2loopback_device(label: str = DESKTOP_CAM_LABEL) -> Optional[str]:
    """Return /dev/videoN for the v4l2loopback device with this card
    label, or None if it is not loaded."""
    for name_path in sorted(glob.glob('/sys/class/video4linux/video*/name')):
        try:
            with open(name_path, encoding='utf-8') as f:
                name = f.read().strip()
        except OSError:
            continue
        if name == label:
            m = re.search(r'video(\d+)/name$', name_path)
            if m:
                return f'/dev/video{m.group(1)}'
    return None


def ensure_v4l2loopback_device(label: str = DESKTOP_CAM_LABEL) -> str:
    """Find the virtual desktop-capture camera device, or raise a clear,
    actionable error pointing at the one-time setup script."""
    dev = find_v4l2loopback_device(label)
    if dev is not None:
        return dev
    raise DesktopCaptureError(
        "No v4l2loopback device found with card_label='" + label + "'. "
        "Run examples/desktop/setup_desktop_capture.sh once (it needs sudo "
        "to load the v4l2loopback kernel module), then try again."
    )


def find_alsa_loopback_card_index() -> Optional[int]:
    """Return the ALSA card index of the 'Loopback' card (snd-aloop), or
    None if it is not loaded."""
    cards_file = '/proc/asound/cards'
    if not os.path.exists(cards_file):
        return None
    try:
        with open(cards_file, encoding='utf-8') as f:
            content = f.read()
    except OSError:
        return None
    # Example line: " 0 [Loopback       ]: Loopback - Loopback"
    for line in content.splitlines():
        m = re.match(r'\s*(\d+)\s*\[([^\]]*)\]', line)
        if m and m.group(2).strip() == 'Loopback':
            return int(m.group(1))
    return None


def ensure_alsa_loopback() -> Tuple[str, str]:
    """Return (playback_hw, capture_hw) ALSA device strings for the
    snd-aloop pair, or raise a clear, actionable error."""
    idx = find_alsa_loopback_card_index()
    if idx is None:
        raise DesktopCaptureError(
            "No ALSA 'Loopback' card found (snd-aloop not loaded). "
            "Run examples/desktop/setup_desktop_capture.sh once (it needs "
            "sudo to load the snd-aloop kernel module), then try again."
        )
    return f'hw:{idx},0,0', f'hw:{idx},1,0'


def find_real_webcam(exclude_device: Optional[str] = None) -> Optional[str]:
    """Best-effort: find a physical camera that is not our own virtual
    loopback device, for the 'switch to camera' menu option. Returns
    None if there is no physical webcam -- switching is then simply
    unavailable, not an error."""
    from robonet.gst_io.devices import find_camera_devices
    for dev in find_camera_devices():
        if dev != exclude_device:
            return dev
    return None


# ---------------------------------------------------------------------------
# Feeder pipelines
# ---------------------------------------------------------------------------

class DesktopVideoFeeder:
    """Continuously captures the X11 desktop and writes it into a
    v4l2loopback device, so the existing v4l2src-based GstSender can pick
    it up as if it were a normal webcam. Runs its own GLib main loop
    thread, matching the pattern GstSender/GstReceiver already use."""

    def __init__(self, loopback_device: str,
                 width: int = 1920, height: int = 1080, fps: int = 30):
        self._loopback_device = loopback_device
        self._width  = width
        self._height = height
        self._fps    = fps
        self._pipeline: Optional[Gst.Pipeline] = None
        self._glib_loop:   Optional[GLib.MainLoop]    = None
        self._glib_thread: Optional[threading.Thread] = None

    def build(self) -> bool:
        desc = (
            f'ximagesrc use-damage=false ! '
            f'video/x-raw,framerate={self._fps}/1 ! '
            f'videoconvert ! videoscale ! '
            f'video/x-raw,width={self._width},height={self._height},format=YUY2 ! '
            f'v4l2sink device={self._loopback_device} sync=false'
        )
        try:
            self._pipeline = Gst.parse_launch(desc)
        except GLib.Error as e:
            log.error(f'[desktop-capture] failed to build video feeder: {e}')
            return False
        return True

    def _on_bus_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[desktop-capture] video feeder error: {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[desktop-capture] video feeder warning: {warn} | {dbg}')

    def start(self) -> bool:
        if self._pipeline is None and not self.build():
            return False
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_bus_message)
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.error('[desktop-capture] video feeder failed to reach PLAYING '
                      '(is the desktop an X11 session? ximagesrc needs X11)')
            return False
        self._glib_loop = GLib.MainLoop()
        self._glib_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name='desktop-video-feeder-glib')
        self._glib_thread.start()
        log.info(f'[desktop-capture] video feeder -> {self._loopback_device} started')
        return True

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        if self._glib_loop and self._glib_loop.is_running():
            self._glib_loop.quit()
        self._glib_loop = None


class DesktopAudioFeeder:
    """Mixes desktop system audio (the default sink's monitor) with the
    machine's real microphone (if present) and writes the result into an
    ALSA loopback playback device, so the existing alsasrc-based
    GstSender can capture 'what the desktop hears and says' as if it
    were a normal microphone.

    Degrades gracefully: if no PulseAudio-compatible sink/source is
    found, start() returns False and logs a warning rather than raising
    -- desktop capture continues with video only.
    """

    def __init__(self, loopback_playback_hw: str,
                 sample_rate: int = 48000, include_mic: bool = True):
        self._loopback_hw = loopback_playback_hw
        self._sample_rate = sample_rate
        self._include_mic = include_mic
        self._pipeline: Optional[Gst.Pipeline] = None
        self._glib_loop:   Optional[GLib.MainLoop]    = None
        self._glib_thread: Optional[threading.Thread] = None

    @staticmethod
    def _pactl_get(field: str) -> Optional[str]:
        try:
            out = subprocess.run(
                ['pactl', f'get-default-{field}'],
                capture_output=True, text=True, timeout=3)
            val = out.stdout.strip()
            return val or None
        except Exception:
            return None

    def build(self) -> bool:
        sink = self._pactl_get('sink')
        monitor = f'{sink}.monitor' if sink else None
        mic = self._pactl_get('source') if self._include_mic else None
        if mic and monitor and mic == monitor:
            mic = None  # don't double up if the "source" is the monitor itself

        branches = []
        if monitor:
            branches.append(f'pulsesrc device={monitor} ! audioconvert ! audioresample ! mix.')
        if mic:
            branches.append(f'pulsesrc device={mic} ! audioconvert ! audioresample ! mix.')

        if not branches:
            log.warning('[desktop-capture] no pulseaudio/pipewire sink or source found; '
                        'audio feeder disabled, continuing with video only')
            return False

        desc = (
            f'audiomixer name=mix ! audioconvert ! audioresample ! '
            f'audio/x-raw,rate={self._sample_rate},channels=1 ! '
            f'alsasink device={self._loopback_hw} sync=false '
            + ' '.join(branches)
        )
        try:
            self._pipeline = Gst.parse_launch(desc)
        except GLib.Error as e:
            log.error(f'[desktop-capture] failed to build audio feeder: {e}')
            return False
        return True

    def _on_bus_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[desktop-capture] audio feeder error: {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[desktop-capture] audio feeder warning: {warn} | {dbg}')

    def start(self) -> bool:
        if self._pipeline is None and not self.build():
            return False
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_bus_message)
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.error('[desktop-capture] audio feeder failed to reach PLAYING')
            return False
        self._glib_loop = GLib.MainLoop()
        self._glib_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name='desktop-audio-feeder-glib')
        self._glib_thread.start()
        log.info(f'[desktop-capture] audio feeder -> {self._loopback_hw} started')
        return True

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        if self._glib_loop and self._glib_loop.is_running():
            self._glib_loop.quit()
        self._glib_loop = None
