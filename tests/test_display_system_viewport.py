"""
tests/test_display_system_viewport.py

Tests DisplaySubSystem's zoom/pan wiring: edge-pan detection (checked
every frame in run_once, not just on mouse-move) and scroll-to-zoom.
The Viewport math itself is tested in tests/test_viewport.py -- this
file is about DisplaySubSystem actually driving it correctly.
"""

import unittest
from unittest.mock import MagicMock

import numpy as np


def _stub_out_missing_displayarray_font_submodule():
    """displayarray.window.mglwindow imports displayarray.font.get_texture_atlas,
    which doesn't exist in every installed displayarray build (it's on an
    actively-developed branch). Stub it out since this file never touches
    font rendering at all -- this only exists to make display_system
    importable, not to test displayarray itself."""
    import sys, types
    if 'displayarray.font.get_texture_atlas' in sys.modules:
        return
    try:
        import displayarray.font.get_texture_atlas  # noqa: F401
        return  # the real thing is present -- nothing to stub
    except ImportError:
        pass
    fake_font_pkg = types.ModuleType('displayarray.font')
    fake_atlas_mod = types.ModuleType('displayarray.font.get_texture_atlas')
    fake_atlas_mod.get_or_create_font_npz = lambda *a, **kw: None
    sys.modules['displayarray.font'] = fake_font_pkg
    sys.modules['displayarray.font.get_texture_atlas'] = fake_atlas_mod


_stub_out_missing_displayarray_font_submodule()

from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.util.viewport import Viewport


def _make_sub(source_shape=(1080, 1920, 3), out_res=(640, 480)):
    sub = DisplaySubSystem.__new__(DisplaySubSystem)
    sub.in_img = np.zeros(source_shape, dtype=np.uint8)
    sub.in_aud = None
    sub.displayer = MagicMock()
    sub.frame_time = 1.0 / 30
    sub.out_res = out_res
    sub.viewport = Viewport()
    sub._edit_mouse_pos = None
    sub._audio_stream = None
    return sub


class TestEdgePan(unittest.TestCase):

    def test_no_pan_tracking_when_not_in_edit_mode(self):
        sub = _make_sub()
        sub._edit_mouse_pos = None  # never entered edit mode

        sub._check_edge_pan(1920, 1080)

        self.assertEqual((sub.viewport.pan_x, sub.viewport.pan_y), (0.5, 0.5))

    def test_no_pan_at_baseline_zoom_even_near_edge(self):
        # Nowhere to pan to at zoom=1.0 -- the whole frame is already visible.
        sub = _make_sub()
        sub._edit_mouse_pos = (0.02, 0.5)  # near the left edge

        sub._check_edge_pan(1920, 1080)

        self.assertEqual(sub.viewport.pan_x, 0.5)

    def test_pans_toward_edge_after_zooming_in(self):
        sub = _make_sub()
        sub.viewport.zoom_by(sub.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        sub._edit_mouse_pos = (0.02, 0.5)  # near the left edge

        sub._check_edge_pan(1920, 1080)

        self.assertLess(sub.viewport.pan_x, 0.5)  # panned left

    def test_no_pan_when_mouse_is_away_from_any_edge(self):
        sub = _make_sub()
        sub.viewport.zoom_by(sub.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        original_pan = (sub.viewport.pan_x, sub.viewport.pan_y)
        sub._edit_mouse_pos = (0.5, 0.5)  # dead center

        sub._check_edge_pan(1920, 1080)

        self.assertEqual((sub.viewport.pan_x, sub.viewport.pan_y), original_pan)

    def test_pan_speed_scales_with_how_close_to_the_edge(self):
        sub = _make_sub()
        sub.viewport.zoom_by(sub.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        sub.viewport.pan_x = 0.5  # room to pan in both directions from here

        sub._edit_mouse_pos = (0.09, 0.5)  # just inside the 10% edge band
        sub._check_edge_pan(1920, 1080)
        small_pan = 0.5 - sub.viewport.pan_x

        sub2 = _make_sub()
        sub2.viewport.zoom_by(sub2.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        sub2.viewport.pan_x = 0.5
        sub2._edit_mouse_pos = (0.01, 0.5)  # right at the edge
        sub2._check_edge_pan(1920, 1080)
        large_pan = 0.5 - sub2.viewport.pan_x

        self.assertGreater(large_pan, small_pan)  # closer to the edge pans faster


class TestEditModeScroll(unittest.TestCase):

    def test_scroll_up_zooms_in(self):
        sub = _make_sub()
        sub._on_edit_scroll(1.0)
        self.assertGreater(sub.viewport.zoom, 1.0)

    def test_scroll_down_at_baseline_stays_at_minimum(self):
        sub = _make_sub()
        sub._on_edit_scroll(-1.0)
        self.assertEqual(sub.viewport.zoom, 1.0)  # can't zoom out past the whole-frame baseline

    def test_scroll_zero_does_not_change_zoom(self):
        sub = _make_sub()
        sub._on_edit_scroll(0.0)
        self.assertEqual(sub.viewport.zoom, 1.0)

    def test_repeated_scroll_up_approaches_but_respects_max_zoom(self):
        sub = _make_sub()
        max_z = sub.viewport.max_zoom(1920, 1080, 640, 480)
        for _ in range(200):
            sub._on_edit_scroll(1.0)
        self.assertLessEqual(sub.viewport.zoom, max_z + 1e-9)


class TestEditModeMouseMove(unittest.TestCase):

    def test_records_swapped_position(self):
        sub = _make_sub()
        sub._on_edit_mouse_move(0.3, 0.7)  # tx, ty as displayarray reports them
        self.assertEqual(sub._edit_mouse_pos, (0.7, 0.3))  # swapped, matching pass-through mode


if __name__ == '__main__':
    unittest.main()
