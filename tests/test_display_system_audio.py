"""
tests/test_display_system_audio.py

Tests robonet/brain/display_system.py's audio handling:
  - reshape_to_square_matrix: turns a 1D array into the most-square 2D
    shape that evenly divides it (displayarray needs at least 2D).
  - run_once's audio normalization: raw PCM audio is roughly -1..1, but
    displayarray multiplies float input by 255 assuming it's already
    0..1 -- without remapping, the negative half of every waveform
    wrapped around via uint8 underflow, which is what actually produced
    the mostly-black-and-white display Simleek found. Fixed with
    (aud/2)+0.5.
"""

import unittest
from unittest.mock import MagicMock, patch
import asyncio

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

from robonet.brain.display_system import reshape_to_square_image, DisplaySubSystem


class TestReshapeToSquareImageMono(unittest.TestCase):
    """Mono (1D) input: stays a plain 2D array -- pad_to_rgb only
    triggers for arrays that already have a trailing channel axis with
    fewer than 3 channels, and a 1D array reshapes straight to 2D with
    no such axis at all."""

    def test_perfect_square_reshapes_square(self):
        arr = np.arange(16)
        result = reshape_to_square_image(arr)
        self.assertEqual(result.shape, (4, 4))

    def test_prime_length_falls_back_to_one_row(self):
        arr = np.arange(13)  # prime -- only divisors are 1 and 13
        result = reshape_to_square_image(arr)
        self.assertEqual(result.shape, (1, 13))

    def test_picks_the_most_square_factor_pair(self):
        arr = np.arange(24)  # factor pairs: 1x24, 2x12, 3x8, 4x6 -- 4x6 is most square
        result = reshape_to_square_image(arr)
        self.assertEqual(result.shape, (4, 6))

    def test_preserves_all_elements(self):
        arr = np.arange(30)
        result = reshape_to_square_image(arr)
        self.assertEqual(result.size, 30)
        np.testing.assert_array_equal(result.flatten(), arr)


class TestAudioNormalization(unittest.TestCase):
    """run_once's -1..1 -> 0..1 remap, via direct arithmetic checks
    (the same operation run_once applies) rather than driving the whole
    async loop, since the interesting part is the transform itself."""

    def test_remaps_full_negative_to_zero(self):
        aud = np.array([-1.0, -1.0, -1.0, -1.0], dtype=np.float32)
        normalized = (aud / 2.0) + 0.5
        np.testing.assert_array_almost_equal(normalized, [0.0, 0.0, 0.0, 0.0])

    def test_remaps_full_positive_to_one(self):
        aud = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        normalized = (aud / 2.0) + 0.5
        np.testing.assert_array_almost_equal(normalized, [1.0, 1.0, 1.0, 1.0])

    def test_remaps_silence_to_midpoint(self):
        aud = np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        normalized = (aud / 2.0) + 0.5
        np.testing.assert_array_almost_equal(normalized, [0.5, 0.5, 0.5, 0.5])

    def test_result_stays_within_0_and_1_for_typical_pcm_range(self):
        rng = np.random.default_rng(0)
        aud = rng.uniform(-1.0, 1.0, size=1000).astype(np.float32)
        normalized = (aud / 2.0) + 0.5
        self.assertTrue(np.all(normalized >= 0.0))
        self.assertTrue(np.all(normalized <= 1.0))


class TestRunOnceAppliesNormalization(unittest.TestCase):
    """Confirms run_once actually applies the remap to what gets handed
    to the displayer, not just that the arithmetic itself is correct."""

    def _make_sub(self, in_aud):
        from robonet.brain.util.viewport import Viewport
        sub = DisplaySubSystem.__new__(DisplaySubSystem)
        sub.in_img = np.zeros((4, 4, 3), dtype=np.uint8)
        sub.in_aud = in_aud
        sub.displayer = MagicMock()
        sub.frame_time = 0.0
        sub.out_res = (4, 4)
        sub.viewport = Viewport()
        sub._edit_mouse_pos = None
        sub._audio_stream = None
        sub._fullscreen_key_disabled = True  # not under test here -- see test_disable_fullscreen_key.py
        return sub

    def _run(self, coro):
        """asyncio.run() closes its event loop when done, which breaks
        other tests elsewhere relying on an implicit 'current' event
        loop still existing (the older asyncio.ensure_future() pattern).
        Setting one explicitly and not closing it avoids that."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def test_negative_samples_get_remapped_before_display(self):
        aud = np.array([-1.0, 0.0, 1.0, 0.5], dtype=np.float32)
        sub = self._make_sub(aud)

        self._run(sub.run_once(MagicMock()))

        call = [c for c in sub.displayer.update.call_args_list if c.args[1] == 'audio']
        self.assertEqual(len(call), 1)
        displayed = call[0].args[0]
        self.assertTrue(np.all(displayed >= 0.0))
        self.assertTrue(np.all(displayed <= 1.0))

    def test_none_audio_skips_display_update_for_audio(self):
        sub = self._make_sub(None)

        self._run(sub.run_once(MagicMock()))

        audio_calls = [c for c in sub.displayer.update.call_args_list if c.args[1] == 'audio']
        self.assertEqual(len(audio_calls), 0)


if __name__ == '__main__':
    unittest.main()


class TestUpdateAudioFormats(unittest.TestCase):
    """update_audio accepts None (early return, no dtype access), raw
    int16 (converted to float32 [-1, 1]), and float input unchanged."""

    def _make_sub(self):
        return DisplaySubSystem.__new__(DisplaySubSystem)

    def test_none_does_not_crash_on_dtype_access(self):
        # Regression: aud.dtype was checked before the None check.
        sub = self._make_sub()
        sub.update_audio(None)  # must not raise
        self.assertIsNone(sub.in_aud)

    def test_int16_input_is_normalized_to_float(self):
        sub = self._make_sub()
        sub.update_audio(np.array([0, 16384, -16384], dtype=np.int16))
        np.testing.assert_allclose(sub.in_aud, [0.0, 0.5, -0.5], atol=1e-6)
        self.assertEqual(sub.in_aud.dtype, np.float32)

    def test_float_input_passes_through_as_float32(self):
        sub = self._make_sub()
        sub.update_audio(np.array([0.25, -0.25], dtype=np.float64))
        np.testing.assert_allclose(sub.in_aud, [0.25, -0.25])
        self.assertEqual(sub.in_aud.dtype, np.float32)


class TestReshapeToSquareImageStereo(unittest.TestCase):
    """2-channel images are awkward to display (RGB/RGBA is the
    standard, not 2) -- pad_to_rgb zero-pads the third channel rather
    than inventing a meaning for it."""

    def test_output_has_three_channels(self):
        stereo = np.random.rand(64, 2).astype(np.float32)
        result = reshape_to_square_image(stereo)
        self.assertEqual(result.shape[-1], 3)

    def test_third_channel_is_all_zeros(self):
        stereo = np.random.rand(64, 2).astype(np.float32)
        result = reshape_to_square_image(stereo)
        np.testing.assert_array_equal(result[:, :, 2], np.zeros(result.shape[:2]))

    def test_left_and_right_land_in_the_correct_channels(self):
        left = np.arange(64, dtype=np.float32)
        right = np.arange(64, 128, dtype=np.float32)
        stereo = np.stack([left, right], axis=1)

        result = reshape_to_square_image(stereo)

        self.assertEqual(set(result[:, :, 0].flatten()), set(left))
        self.assertEqual(set(result[:, :, 1].flatten()), set(right))

    def test_all_three_channels_share_the_same_2d_shape(self):
        stereo = np.random.rand(100, 2).astype(np.float32)
        result = reshape_to_square_image(stereo)
        self.assertEqual(result.ndim, 3)
        rows, cols, ch = result.shape
        self.assertEqual(rows * cols, 100)
        self.assertEqual(ch, 3)

    def test_pad_to_rgb_false_leaves_channel_count_unchanged(self):
        stereo = np.random.rand(64, 2).astype(np.float32)
        result = reshape_to_square_image(stereo, pad_to_rgb=False)
        self.assertEqual(result.shape[-1], 2)


class TestRunOnceDispatchesByAudioShape(unittest.TestCase):
    """run_once must route mono (1D) to the existing grayscale square
    path unchanged, and stereo (2D) to the new 3-channel path."""

    def _make_sub(self, in_aud):
        from robonet.brain.util.viewport import Viewport
        sub = DisplaySubSystem.__new__(DisplaySubSystem)
        sub.in_img = np.zeros((4, 4, 3), dtype=np.uint8)
        sub.in_aud = in_aud
        sub.displayer = MagicMock()
        sub.frame_time = 0.0
        sub.out_res = (4, 4)
        sub.viewport = Viewport()
        sub._edit_mouse_pos = None
        sub._fullscreen_key_disabled = True
        return sub

    def test_mono_audio_still_produces_a_2d_array(self):
        sub = self._make_sub(np.random.uniform(-1, 1, 64).astype(np.float32))
        asyncio.run(sub.run_once(MagicMock()))
        sent = sub.displayer.update.call_args_list[-1].args[0]
        self.assertEqual(sent.ndim, 2)

    def test_stereo_audio_produces_a_3channel_array(self):
        stereo = np.random.uniform(-1, 1, (64, 2)).astype(np.float32)
        sub = self._make_sub(stereo)
        asyncio.run(sub.run_once(MagicMock()))
        sent = sub.displayer.update.call_args_list[-1].args[0]
        self.assertEqual(sent.ndim, 3)
        self.assertEqual(sent.shape[-1], 3)
