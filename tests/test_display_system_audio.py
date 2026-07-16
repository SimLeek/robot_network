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


class TestResolveSpeakerDevice(unittest.TestCase):
    """Regression coverage: sd.OutputStream() was never given a device=,
    relying entirely on sounddevice's own implicit default -- on Linux
    that can land on the first raw ALSA hardware device rather than the
    one actually configured as the system output (pulse/pipewire)."""

    def _make_sub(self):
        sub = DisplaySubSystem.__new__(DisplaySubSystem)
        sub._audio_stream = None
        return sub

    def test_explicit_setting_takes_priority_over_auto_detect(self):
        sub = self._make_sub()
        with patch('robonet.brain.display_system.settings', {'speaker_device': 3}):
            self.assertEqual(sub._resolve_speaker_device(), 3)

    def test_prefers_a_pulse_named_device_when_none_configured(self):
        sub = self._make_sub()
        devices = [
            {'name': 'HDA Intel PCH: ALC892 Analog', 'max_output_channels': 2},
            {'name': 'pulse', 'max_output_channels': 32},
        ]
        with patch('robonet.brain.display_system.settings', {'speaker_device': None}), \
             patch('robonet.brain.display_system.sd.query_devices', return_value=devices):
            self.assertEqual(sub._resolve_speaker_device(), 1)

    def test_falls_back_to_pipewire_if_no_pulse_device(self):
        sub = self._make_sub()
        devices = [
            {'name': 'HDA Intel PCH: ALC892 Analog', 'max_output_channels': 2},
            {'name': 'pipewire', 'max_output_channels': 32},
        ]
        with patch('robonet.brain.display_system.settings', {'speaker_device': None}), \
             patch('robonet.brain.display_system.sd.query_devices', return_value=devices):
            self.assertEqual(sub._resolve_speaker_device(), 1)

    def test_falls_back_to_none_when_nothing_preferred_found(self):
        sub = self._make_sub()
        devices = [{'name': 'HDA Intel PCH: ALC892 Analog', 'max_output_channels': 2}]
        with patch('robonet.brain.display_system.settings', {'speaker_device': None}), \
             patch('robonet.brain.display_system.sd.query_devices', return_value=devices):
            self.assertIsNone(sub._resolve_speaker_device())

    def test_query_failure_falls_back_to_none_rather_than_raising(self):
        sub = self._make_sub()
        with patch('robonet.brain.display_system.settings', {'speaker_device': None}), \
             patch('robonet.brain.display_system.sd.query_devices', side_effect=OSError('no backend')):
            self.assertIsNone(sub._resolve_speaker_device())


class TestAudioCallbackRobustness(unittest.TestCase):
    """The callback runs on PortAudio's own thread, where an escaping
    exception ABORTS THE STREAM -- sounddevice only prints to stderr,
    so playback just silently stops forever (nothing in pavucontrol).
    These verify the callback can no longer die that way, and that
    chunk/frame size mismatches no longer discard audio."""

    def _make_sub(self):
        import queue as queue_mod
        sub = DisplaySubSystem.__new__(DisplaySubSystem)
        sub._audio_stream = None
        sub._audio_queue = queue_mod.Queue(maxsize=8)
        sub._audio_leftover = np.zeros(0, dtype=np.float32)
        sub._audio_started_playing = True  # silence the first-play INFO log in tests
        return sub

    def _out(self, frames):
        return np.zeros((frames, 1), dtype=np.float32)

    def test_chunk_smaller_than_frames_pads_with_silence(self):
        sub = self._make_sub()
        sub._audio_queue.put(np.full(4, 0.5, dtype=np.float32))
        out = self._out(8)

        sub._audio_cb(out, 8, None, None)

        np.testing.assert_array_equal(out[:4, 0], np.full(4, 0.5, dtype=np.float32))
        np.testing.assert_array_equal(out[4:, 0], np.zeros(4, dtype=np.float32))

    def test_oversized_chunk_tail_is_kept_for_the_next_callback(self):
        # Regression: the old callback threw the tail away entirely.
        sub = self._make_sub()
        sub._audio_queue.put(np.arange(10, dtype=np.float32))
        out1 = self._out(6)
        out2 = self._out(6)

        sub._audio_cb(out1, 6, None, None)
        sub._audio_cb(out2, 6, None, None)

        np.testing.assert_array_equal(out1[:, 0], np.arange(6, dtype=np.float32))
        np.testing.assert_array_equal(out2[:4, 0], np.arange(6, 10, dtype=np.float32))

    def test_multiple_small_chunks_get_concatenated(self):
        sub = self._make_sub()
        sub._audio_queue.put(np.full(3, 0.1, dtype=np.float32))
        sub._audio_queue.put(np.full(3, 0.2, dtype=np.float32))
        out = self._out(6)

        sub._audio_cb(out, 6, None, None)

        np.testing.assert_allclose(out[:3, 0], 0.1)
        np.testing.assert_allclose(out[3:, 0], 0.2)

    def test_empty_queue_outputs_silence_without_raising(self):
        sub = self._make_sub()
        out = self._out(8)
        out.fill(9.9)  # pre-fill garbage to confirm it gets zeroed

        sub._audio_cb(out, 8, None, None)

        np.testing.assert_array_equal(out, np.zeros((8, 1), dtype=np.float32))

    def test_a_poisoned_chunk_cannot_abort_the_stream(self):
        # The critical guarantee: even a chunk that would raise during
        # assignment logs-and-silences instead of letting the exception
        # escape (which would kill the stream permanently).
        sub = self._make_sub()
        sub._audio_queue.put('not an array at all')
        out = self._out(8)

        sub._audio_cb(out, 8, None, None)  # must not raise

    def test_update_audio_flattens_column_shaped_chunks(self):
        # A (N,1)-shaped chunk assigned into outdata[:n, 0] raises in
        # the callback -- update_audio must flatten before queueing.
        sub = self._make_sub()
        sub.in_aud = None

        sub.update_audio(np.zeros((4, 1), dtype=np.float64))

        queued = sub._audio_queue.get_nowait()
        self.assertEqual(queued.ndim, 1)
        self.assertEqual(queued.dtype, np.float32)

    def test_update_audio_drops_oldest_not_newest_when_full(self):
        sub = self._make_sub()
        for i in range(8):
            sub.update_audio(np.full(2, float(i), dtype=np.float32))

        sub.update_audio(np.full(2, 99.0, dtype=np.float32))  # 9th chunk: queue full

        chunks = []
        while not sub._audio_queue.empty():
            chunks.append(sub._audio_queue.get_nowait()[0])
        self.assertNotIn(0.0, chunks)   # oldest was dropped
        self.assertIn(99.0, chunks)     # newest was kept


class TestUpdateAudioFormats(unittest.TestCase):
    """update_audio accepts None (early return, no dtype access), raw
    int16 (converted to float32 [-1, 1]), and float input unchanged."""

    def _make_sub(self):
        import queue as queue_mod
        sub = DisplaySubSystem.__new__(DisplaySubSystem)
        sub._audio_stream = None
        sub._audio_queue = queue_mod.Queue(maxsize=8)
        return sub

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
