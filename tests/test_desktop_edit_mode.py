"""
tests/test_desktop_edit_mode.py

Tests DesktopSubSystem's zoom/pan wiring: edge-pan detection (checked
every frame by MenuSubSystem's render loop, not just on mouse-move),
scroll-to-zoom, and that af_edit's handlers get (re-)bound on every
connect via _bind_input -- the actual fix for zoom/pan silently
stopping after the first connection, since swap_subsystem's
unbind_all() + re-register cycle wiped out bindings that were only
ever set up once, in DisplaySubSystem.setup().

The Viewport math itself is tested in tests/test_viewport.py -- this
file is about DesktopSubSystem actually driving it correctly.
"""

import unittest
from unittest.mock import MagicMock

import numpy as np

from robonet.brain.desktop_system import DesktopSubSystem
from robonet.brain.util.viewport import Viewport


def _make_sub(source_shape=(1080, 1920, 3), out_res=(640, 480), screen_width=1920, screen_height=1080):
    endpoint = MagicMock()
    endpoint.streams = [{'name': 'screen', 'type': 'video', 'width': screen_width, 'height': screen_height}]
    sub = DesktopSubSystem(endpoint=endpoint)
    sub._root = MagicMock()
    sub._root.menu.visible = False
    sub._root.displayer.in_img = np.zeros(source_shape, dtype=np.uint8)
    sub._root.displayer.out_res = out_res
    return sub


class TestEdgePan(unittest.TestCase):

    def test_no_pan_tracking_when_not_in_edit_mode(self):
        sub = _make_sub()
        sub._edit_mouse_pos = None  # never entered edit mode

        sub.check_edge_pan(1920, 1080, 640, 480, frame_time=1.0 / 30)

        self.assertEqual((sub.viewport.pan_x, sub.viewport.pan_y), (0.5, 0.5))

    def test_no_pan_at_baseline_zoom_even_near_edge(self):
        # Nowhere to pan to at zoom=1.0 -- the whole frame is already visible.
        sub = _make_sub()
        sub._edit_mouse_pos = (0.02, 0.5)  # near the left edge

        sub.check_edge_pan(1920, 1080, 640, 480, frame_time=1.0 / 30)

        self.assertEqual(sub.viewport.pan_x, 0.5)

    def test_pans_toward_edge_after_zooming_in(self):
        sub = _make_sub()
        sub.viewport.zoom_by(sub.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        sub._edit_mouse_pos = (0.02, 0.5)  # near the left edge

        sub.check_edge_pan(1920, 1080, 640, 480, frame_time=1.0 / 30)

        self.assertLess(sub.viewport.pan_x, 0.5)  # panned left

    def test_no_pan_when_mouse_is_away_from_any_edge(self):
        sub = _make_sub()
        sub.viewport.zoom_by(sub.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        original_pan = (sub.viewport.pan_x, sub.viewport.pan_y)
        sub._edit_mouse_pos = (0.5, 0.5)  # dead center

        sub.check_edge_pan(1920, 1080, 640, 480, frame_time=1.0 / 30)

        self.assertEqual((sub.viewport.pan_x, sub.viewport.pan_y), original_pan)

    def test_pan_speed_scales_with_how_close_to_the_edge(self):
        sub = _make_sub()
        sub.viewport.zoom_by(sub.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        sub.viewport.pan_x = 0.5

        sub._edit_mouse_pos = (0.09, 0.5)  # just inside the 10% edge band
        sub.check_edge_pan(1920, 1080, 640, 480, frame_time=1.0 / 30)
        small_pan = 0.5 - sub.viewport.pan_x

        sub2 = _make_sub()
        sub2.viewport.zoom_by(sub2.viewport.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        sub2.viewport.pan_x = 0.5
        sub2._edit_mouse_pos = (0.01, 0.5)  # right at the edge
        sub2.check_edge_pan(1920, 1080, 640, 480, frame_time=1.0 / 30)
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
        self.assertEqual(sub.viewport.zoom, 1.0)

    def test_scroll_zero_does_not_change_zoom(self):
        sub = _make_sub()
        sub._on_edit_scroll(0.0)
        self.assertEqual(sub.viewport.zoom, 1.0)

    def test_repeated_scroll_up_respects_max_zoom(self):
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


class TestAfEditRebinding(unittest.TestCase):
    """Regression test for the actual bug: af_edit's handlers were only
    ever bound once, in DisplaySubSystem.setup() -- swap_subsystem's
    unbind_all() + re-register cycle on every connect wiped them out
    with nothing re-establishing them, so zoom/pan silently stopped
    working after the first connection."""

    def test_bind_input_binds_af_edit_handlers(self):
        sub = _make_sub()
        sub._bind_input()

        sub._root.displayer.af_edit.bind_mouse_move.assert_called_once_with(sub._on_edit_mouse_move)
        sub._root.displayer.af_edit.bind_mouse_scroll.assert_called_once_with(sub._on_edit_scroll)

    def test_bind_input_rebinds_af_edit_on_every_call(self):
        # Simulates reconnecting: _bind_input gets called again (via a
        # fresh DesktopSubSystem instance, since start() is per-instance),
        # and af_edit's binding calls must happen again each time, not
        # just the first.
        sub1 = _make_sub()
        sub1._bind_input()
        sub2 = _make_sub()
        sub2._root = sub1._root  # same displayer/af_edit, as if reconnecting
        sub2._bind_input()

        self.assertEqual(sub2._root.displayer.af_edit.bind_mouse_move.call_count, 2)

    def test_unbind_input_unbinds_af_edit_handlers(self):
        sub = _make_sub()
        sub._bind_input()
        sub._unbind_input()

        sub._root.displayer.af_edit.unbind_mouse_move.assert_called_once()
        sub._root.displayer.af_edit.unbind_mouse_scroll.assert_called_once()


if __name__ == '__main__':
    unittest.main()
