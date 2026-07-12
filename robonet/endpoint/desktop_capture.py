"""
robonet/endpoint/desktop_capture.py

Errors raised by desktop-mode capture. Desktop video/audio now capture
directly (ximagesrc/pulsesrc) straight into GstSender's normal pipeline
-- see VIDEO_SOURCE_XIMAGESRC/AUDIO_SOURCE_DESKTOP_MIX in
robonet/gst_io/streamer_unencrypted.py -- rather than through a
v4l2loopback/ALSA-loopback intermediary, which produced blank output in
practice and needed kernel modules loaded for no benefit.
"""

from __future__ import annotations


class DesktopCaptureError(Exception):
    """Raised when a desktop-capture prerequisite (e.g. pyautogui/DISPLAY) is missing."""
    pass
