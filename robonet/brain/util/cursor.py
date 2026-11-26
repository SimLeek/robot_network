"""Draw a mouse cursor into a frame (for an AI that needs to see the pointer)."""

from __future__ import annotations

import numpy as np
import cv2

# Classic arrow, tip (hotspot) at (0, 0), in units of its height.
_ARROW = np.array([(0, 0), (0, 0.80), (0.20, 0.62), (0.33, 0.95), (0.45, 0.90),
                   (0.32, 0.58), (0.58, 0.58)], dtype=np.float32)


def draw_cursor(frame: np.ndarray, x: float, y: float, height: float) -> np.ndarray:
    """Draws a white arrow with a black outline, tip at canvas pixel (x, y),
    `height` pixels tall, in place; returns the frame."""
    pts = np.round(_ARROW * height + np.array([x, y], dtype=np.float32)).astype(np.int32)
    thickness = max(1, int(round(height / 12)))
    cv2.fillPoly(frame, [pts], (255, 255, 255))
    cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 0), thickness=thickness)
    return frame
