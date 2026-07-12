"""
robonet/brain/util/viewport.py

Display-side zoom/pan for viewing a subregion of a received frame.
Entirely local -- operates on the already-received numpy frame, no
interaction with GStreamer or the network at all. Panning/zooming at
the source would defeat temporal compression on mostly-static desktop
content and massively increase bitrate for no benefit, since the
source is already transmitted at native resolution.

zoom=1.0 always means "whole source frame visible" (the aspect-
preserving fit baseline, letterboxed if the source and display aspect
ratios differ). Max zoom means "1 source pixel per display pixel" --
native resolution, no magnification beyond what the source actually
has to show.
"""

from __future__ import annotations

import numpy as np
import cv2


def compute_crop_and_scale(source_w: int, source_h: int, display_w: int, display_h: int,
                          zoom: float, pan_x: float, pan_y: float) -> tuple:
    """Returns (x0, y0, crop_w, crop_h, out_w, out_h, pad_left, pad_top):
    crop the source frame to (x0, y0, crop_w, crop_h), scale that crop
    to (out_w, out_h) preserving aspect ratio, then place it in a
    display_w x display_h canvas at offset (pad_left, pad_top) --
    out_w/out_h can be smaller than display_w/display_h (needing
    padding/letterboxing) when the ideal crop region would exceed the
    source frame's actual size in one dimension, which happens near
    zoom=1.0 whenever the source and display aspect ratios differ.
    """
    fit_scale = min(display_w / source_w, display_h / source_h)
    actual_scale = fit_scale * zoom

    ideal_crop_w = display_w / actual_scale
    ideal_crop_h = display_h / actual_scale
    crop_w = min(source_w, ideal_crop_w)
    crop_h = min(source_h, ideal_crop_h)

    cx = pan_x * source_w
    cy = pan_y * source_h
    x0 = int(max(0, min(source_w - crop_w, cx - crop_w / 2)))
    y0 = int(max(0, min(source_h - crop_h, cy - crop_h / 2)))
    crop_w = int(crop_w)
    crop_h = int(crop_h)

    out_w = min(display_w, max(1, int(round(crop_w * actual_scale))))
    out_h = min(display_h, max(1, int(round(crop_h * actual_scale))))

    pad_left = (display_w - out_w) // 2
    pad_top = (display_h - out_h) // 2

    return x0, y0, crop_w, crop_h, out_w, out_h, pad_left, pad_top


def valid_pan_range(source_w: int, source_h: int, display_w: int, display_h: int,
                   zoom: float) -> tuple:
    """((x_min, x_max), (y_min, y_max)) -- the range pan_x/pan_y may
    validly occupy at this zoom level. Collapses to (0.5, 0.5) in
    whichever dimension is letterboxed (the whole source dimension is
    already visible, so there's nowhere to pan to)."""
    fit_scale = min(display_w / source_w, display_h / source_h)
    actual_scale = fit_scale * zoom
    ideal_crop_w = display_w / actual_scale
    ideal_crop_h = display_h / actual_scale

    if ideal_crop_w >= source_w:
        x_range = (0.5, 0.5)
    else:
        half = (ideal_crop_w / source_w) / 2
        x_range = (half, 1.0 - half)
    if ideal_crop_h >= source_h:
        y_range = (0.5, 0.5)
    else:
        half = (ideal_crop_h / source_h) / 2
        y_range = (half, 1.0 - half)
    return x_range, y_range


class Viewport:
    """Per-connection zoom/pan state. One instance per DisplaySubSystem."""

    def __init__(self):
        self.zoom = 1.0
        self.pan_x = 0.5
        self.pan_y = 0.5

    def max_zoom(self, source_w: int, source_h: int, display_w: int, display_h: int) -> float:
        fit_scale = min(display_w / source_w, display_h / source_h)
        return 1.0 / fit_scale

    def reset(self):
        self.zoom = 1.0
        self.pan_x = 0.5
        self.pan_y = 0.5

    def zoom_by(self, factor: float, source_w: int, source_h: int, display_w: int, display_h: int):
        """factor > 1 zooms in, < 1 zooms out. Clamped to
        [1.0 (whole frame), max_zoom (1 source px per display px)]."""
        max_z = self.max_zoom(source_w, source_h, display_w, display_h)
        self.zoom = max(1.0, min(max_z, self.zoom * factor))
        self._clamp_pan(source_w, source_h, display_w, display_h)

    def pan_by(self, dx_frac: float, dy_frac: float, source_w: int, source_h: int,
              display_w: int, display_h: int):
        self.pan_x += dx_frac
        self.pan_y += dy_frac
        self._clamp_pan(source_w, source_h, display_w, display_h)

    def _clamp_pan(self, source_w: int, source_h: int, display_w: int, display_h: int):
        (x_min, x_max), (y_min, y_max) = valid_pan_range(source_w, source_h, display_w, display_h, self.zoom)
        self.pan_x = max(x_min, min(x_max, self.pan_x))
        self.pan_y = max(y_min, min(y_max, self.pan_y))

    def apply(self, frame: np.ndarray, display_w: int, display_h: int) -> np.ndarray:
        """Crop/scale/pad frame per current zoom/pan into an exact
        display_w x display_h canvas."""
        source_h, source_w = frame.shape[:2]
        x0, y0, crop_w, crop_h, out_w, out_h, pad_left, pad_top = compute_crop_and_scale(
            source_w, source_h, display_w, display_h, self.zoom, self.pan_x, self.pan_y)
        cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]
        interp = cv2.INTER_NEAREST if self.zoom > 1.0 else cv2.INTER_LINEAR
        scaled = cv2.resize(cropped, (out_w, out_h), interpolation=interp)
        canvas_shape = (display_h, display_w) + frame.shape[2:]
        canvas = np.zeros(canvas_shape, dtype=frame.dtype)
        canvas[pad_top:pad_top + out_h, pad_left:pad_left + out_w] = scaled
        return canvas
