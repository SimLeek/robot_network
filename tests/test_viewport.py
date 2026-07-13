"""
tests/test_viewport.py

Tests robonet/brain/util/viewport.py -- display-side zoom/pan math.
Uses the actual reported scenario throughout: 1920x1080 source into a
640x480 display, where source (16:9) and display (4:3) aspect ratios
differ, so letterboxing is unavoidable at baseline zoom.
"""

import unittest

import numpy as np

from robonet.brain.util.viewport import Viewport, compute_crop_and_scale, valid_pan_range


class TestComputeCropAndScale(unittest.TestCase):

    def test_baseline_zoom_shows_whole_source_letterboxed(self):
        # zoom=1.0: the whole 1920x1080 frame must be visible. Width is
        # the constraining dimension (640/1920 < 480/1080), so height
        # ends up letterboxed (scaled output is 640x360, padded to 480).
        x0, y0, crop_w, crop_h, out_w, out_h, pad_left, pad_top = compute_crop_and_scale(
            1920, 1080, 640, 480, zoom=1.0, pan_x=0.5, pan_y=0.5)

        self.assertEqual((crop_w, crop_h), (1920, 1080))  # whole frame
        self.assertEqual((x0, y0), (0, 0))
        self.assertEqual(out_w, 640)
        self.assertLess(out_h, 480)  # letterboxed -- shorter than the display
        self.assertEqual(pad_left, 0)  # no horizontal padding needed
        self.assertGreater(pad_top, 0)  # vertical padding (top+bottom bars)

    def test_max_zoom_is_native_1_to_1_no_padding(self):
        max_z = Viewport().max_zoom(1920, 1080, 640, 480)
        x0, y0, crop_w, crop_h, out_w, out_h, pad_left, pad_top = compute_crop_and_scale(
            1920, 1080, 640, 480, zoom=max_z, pan_x=0.5, pan_y=0.5)

        self.assertEqual((crop_w, crop_h), (640, 480))  # native 1:1 crop
        self.assertEqual((out_w, out_h), (640, 480))  # fills the display exactly
        self.assertEqual((pad_left, pad_top), (0, 0))  # no padding needed anymore

    def test_crop_never_exceeds_source_bounds(self):
        for zoom in (1.0, 1.5, 2.0, 3.0):
            with self.subTest(zoom=zoom):
                x0, y0, crop_w, crop_h, *_ = compute_crop_and_scale(
                    1920, 1080, 640, 480, zoom=zoom, pan_x=0.9, pan_y=0.9)
                self.assertGreaterEqual(x0, 0)
                self.assertGreaterEqual(y0, 0)
                self.assertLessEqual(x0 + crop_w, 1920)
                self.assertLessEqual(y0 + crop_h, 1080)

    def test_output_never_exceeds_display_bounds(self):
        for zoom in (1.0, 1.2, 2.0, 3.0):
            with self.subTest(zoom=zoom):
                *_, out_w, out_h, pad_left, pad_top = compute_crop_and_scale(
                    1920, 1080, 640, 480, zoom=zoom, pan_x=0.5, pan_y=0.5)
                self.assertLessEqual(pad_left + out_w, 640)
                self.assertLessEqual(pad_top + out_h, 480)

    def test_matching_aspect_ratios_need_no_padding_at_any_zoom(self):
        # 1920x1080 source into a 960x540 display -- both 16:9, so the
        # whole frame should fill exactly with no letterboxing at all.
        for zoom in (1.0, 1.5, 2.0):
            with self.subTest(zoom=zoom):
                *_, out_w, out_h, pad_left, pad_top = compute_crop_and_scale(
                    1920, 1080, 960, 540, zoom=zoom, pan_x=0.5, pan_y=0.5)
                self.assertEqual((out_w, out_h), (960, 540))
                self.assertEqual((pad_left, pad_top), (0, 0))

    def test_pan_shifts_the_crop_origin(self):
        max_z = Viewport().max_zoom(1920, 1080, 640, 480)
        x0_center, y0_center, *_ = compute_crop_and_scale(
            1920, 1080, 640, 480, zoom=max_z, pan_x=0.5, pan_y=0.5)
        x0_right, y0_right, *_ = compute_crop_and_scale(
            1920, 1080, 640, 480, zoom=max_z, pan_x=0.9, pan_y=0.5)

        self.assertGreater(x0_right, x0_center)  # panned toward the right edge
        self.assertEqual(y0_right, y0_center)  # y unaffected


class TestValidPanRange(unittest.TestCase):

    def test_baseline_zoom_forces_pan_to_center(self):
        # At zoom=1.0 the whole frame is visible -- there's nowhere to
        # pan to in either dimension.
        (x_min, x_max), (y_min, y_max) = valid_pan_range(1920, 1080, 640, 480, zoom=1.0)
        self.assertEqual((x_min, x_max), (0.5, 0.5))
        self.assertEqual((y_min, y_max), (0.5, 0.5))

    def test_max_zoom_allows_a_real_pan_range(self):
        max_z = Viewport().max_zoom(1920, 1080, 640, 480)
        (x_min, x_max), (y_min, y_max) = valid_pan_range(1920, 1080, 640, 480, zoom=max_z)
        self.assertLess(x_min, 0.5)
        self.assertGreater(x_max, 0.5)
        self.assertLess(y_min, 0.5)
        self.assertGreater(y_max, 0.5)

    def test_intermediate_zoom_one_axis_still_letterboxed(self):
        # Partway between baseline and max zoom, width may already have
        # room to pan while height (the non-constraining dimension at
        # baseline) still doesn't.
        (x_min, x_max), (y_min, y_max) = valid_pan_range(1920, 1080, 640, 480, zoom=1.1)
        self.assertLess(x_min, 0.5)  # width already has some pan room
        self.assertEqual((y_min, y_max), (0.5, 0.5))  # height still fully visible


class TestViewport(unittest.TestCase):

    def test_starts_at_baseline_centered(self):
        vp = Viewport()
        self.assertEqual(vp.zoom, 1.0)
        self.assertEqual((vp.pan_x, vp.pan_y), (0.5, 0.5))

    def test_zoom_by_clamps_to_valid_range(self):
        vp = Viewport()
        vp.zoom_by(0.5, 1920, 1080, 640, 480)  # zooming out below 1.0
        self.assertEqual(vp.zoom, 1.0)

        max_z = vp.max_zoom(1920, 1080, 640, 480)
        vp.zoom_by(1000.0, 1920, 1080, 640, 480)  # zooming in way past max
        self.assertAlmostEqual(vp.zoom, max_z)

    def test_zoom_in_then_out_returns_to_baseline(self):
        vp = Viewport()
        vp.zoom_by(2.0, 1920, 1080, 640, 480)
        vp.zoom_by(0.5, 1920, 1080, 640, 480)
        self.assertAlmostEqual(vp.zoom, 1.0)

    def test_pan_at_baseline_zoom_is_a_no_op(self):
        vp = Viewport()
        vp.pan_by(0.3, 0.3, 1920, 1080, 640, 480)
        self.assertEqual((vp.pan_x, vp.pan_y), (0.5, 0.5))  # nowhere to pan to yet

    def test_pan_after_zooming_in_actually_moves(self):
        vp = Viewport()
        vp.zoom_by(vp.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        vp.pan_by(0.3, 0.0, 1920, 1080, 640, 480)
        self.assertGreater(vp.pan_x, 0.5)

    def test_pan_clamps_at_source_edge(self):
        vp = Viewport()
        vp.zoom_by(vp.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        vp.pan_by(10.0, 10.0, 1920, 1080, 640, 480)  # way past the edge
        (x_min, x_max), (y_min, y_max) = valid_pan_range(1920, 1080, 640, 480, vp.zoom)
        self.assertAlmostEqual(vp.pan_x, x_max)
        self.assertAlmostEqual(vp.pan_y, y_max)

    def test_reset_returns_to_baseline(self):
        vp = Viewport()
        vp.zoom_by(2.0, 1920, 1080, 640, 480)
        vp.pan_by(0.1, 0.1, 1920, 1080, 640, 480)
        vp.reset()
        self.assertEqual(vp.zoom, 1.0)
        self.assertEqual((vp.pan_x, vp.pan_y), (0.5, 0.5))

    def test_apply_returns_exact_display_size(self):
        vp = Viewport()
        frame = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)

        result = vp.apply(frame, 640, 480)

        self.assertEqual(result.shape, (480, 640, 3))

    def test_apply_at_max_zoom_returns_exact_display_size(self):
        vp = Viewport()
        vp.zoom_by(vp.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        frame = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)

        result = vp.apply(frame, 640, 480)

        self.assertEqual(result.shape, (480, 640, 3))

    def test_apply_preserves_dtype(self):
        vp = Viewport()
        frame = np.random.rand(1080, 1920, 3).astype(np.float32)

        result = vp.apply(frame, 640, 480)

        self.assertEqual(result.dtype, np.float32)

    def test_apply_grayscale_frame(self):
        vp = Viewport()
        frame = np.random.randint(0, 255, (1080, 1920), dtype=np.uint8)

        result = vp.apply(frame, 640, 480)

        self.assertEqual(result.shape, (480, 640))

    def test_inverse_map_at_baseline_zoom_center_is_source_center(self):
        vp = Viewport()
        x_frac, y_frac = vp.inverse_map(0.5, 0.5, 1920, 1080, 640, 480)
        self.assertAlmostEqual(x_frac, 0.5, places=3)
        self.assertAlmostEqual(y_frac, 0.5, places=3)

    def test_inverse_map_on_letterbox_padding_clamps_to_edge(self):
        # At baseline zoom, height is letterboxed (source is 16:9,
        # display is 4:3) -- the very top of the canvas is padding, not
        # actual image content, and should clamp to the source's top
        # edge rather than extrapolating to something nonsensical.
        vp = Viewport()
        x_frac, y_frac = vp.inverse_map(0.5, 0.0, 1920, 1080, 640, 480)
        self.assertAlmostEqual(y_frac, 0.0, places=3)

    def test_inverse_map_is_true_inverse_of_forward_math_at_baseline(self):
        # Pick a source pixel, run it through compute_crop_and_scale's
        # forward direction conceptually (via apply's same math), then
        # confirm inverse_map recovers a canvas position that maps back
        # to (approximately) the same source fraction.
        vp = Viewport()
        source_w, source_h, display_w, display_h = 1920, 1080, 640, 480
        for x_frac_in, y_frac_in in [(0.25, 0.5), (0.75, 0.5), (0.5, 0.5)]:
            x0, y0, crop_w, crop_h, out_w, out_h, pad_left, pad_top = compute_crop_and_scale(
                source_w, source_h, display_w, display_h, vp.zoom, vp.pan_x, vp.pan_y)
            source_px_x = x_frac_in * source_w
            source_px_y = y_frac_in * source_h
            fit_scale = min(display_w / source_w, display_h / source_h)
            canvas_x = pad_left + (source_px_x - x0) * fit_scale
            canvas_y = pad_top + (source_px_y - y0) * fit_scale
            tx_canvas, ty_canvas = canvas_x / display_w, canvas_y / display_h

            x_frac_out, y_frac_out = vp.inverse_map(tx_canvas, ty_canvas, source_w, source_h, display_w, display_h)

            self.assertAlmostEqual(x_frac_out, x_frac_in, places=2)
            self.assertAlmostEqual(y_frac_out, y_frac_in, places=2)

    def test_inverse_map_after_zoom_and_pan_reflects_the_new_view(self):
        vp = Viewport()
        vp.zoom_by(vp.max_zoom(1920, 1080, 640, 480), 1920, 1080, 640, 480)
        vp.pan_by(0.3, 0.0, 1920, 1080, 640, 480)  # panned toward the right

        center_x_frac, _ = vp.inverse_map(0.5, 0.5, 1920, 1080, 640, 480)

        # Canvas center should now correspond to a source position well
        # to the right of the untouched source center (0.5), since
        # we've panned and zoomed into that region.
        self.assertGreater(center_x_frac, 0.6)

    def test_inverse_map_output_always_within_0_and_1(self):
        vp = Viewport()
        vp.zoom_by(2.0, 1920, 1080, 640, 480)
        for tx, ty in [(0.0, 0.0), (1.0, 1.0), (0.5, 0.0), (0.0, 0.5)]:
            with self.subTest(tx=tx, ty=ty):
                x_frac, y_frac = vp.inverse_map(tx, ty, 1920, 1080, 640, 480)
                self.assertGreaterEqual(x_frac, 0.0)
                self.assertLessEqual(x_frac, 1.0)
                self.assertGreaterEqual(y_frac, 0.0)
                self.assertLessEqual(y_frac, 1.0)


if __name__ == '__main__':
    unittest.main()
