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

from robonet.brain.display_system import reshape_to_square_matrix, DisplaySubSystem


class TestReshapeToSquareMatrix(unittest.TestCase):

    def test_perfect_square_reshapes_square(self):
        arr = np.arange(16)
        result = reshape_to_square_matrix(arr)
        self.assertEqual(result.shape, (4, 4))

    def test_prime_length_falls_back_to_one_row(self):
        arr = np.arange(13)  # prime -- only divisors are 1 and 13
        result = reshape_to_square_matrix(arr)
        self.assertEqual(result.shape, (1, 13))

    def test_picks_the_most_square_factor_pair(self):
        arr = np.arange(24)  # factor pairs: 1x24, 2x12, 3x8, 4x6 -- 4x6 is most square
        result = reshape_to_square_matrix(arr)
        self.assertEqual(result.shape, (4, 6))

    def test_preserves_all_elements(self):
        arr = np.arange(30)
        result = reshape_to_square_matrix(arr)
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
        sub = DisplaySubSystem.__new__(DisplaySubSystem)
        sub.in_img = np.zeros((4, 4, 3), dtype=np.uint8)
        sub.in_aud = in_aud
        sub.displayer = MagicMock()
        sub.frame_time = 0.0
        sub._audio_stream = None
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
