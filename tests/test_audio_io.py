"""tests/test_audio_io.py -- the blocksize<->Hz neuron-spec math."""

import unittest

from robonet.audio_io import audio_neuron_spec, blocksize_for_max_hz


class TestAudioNeuronSpec(unittest.TestCase):

    def test_count_equals_blocksize(self):
        spec = audio_neuron_spec(sample_rate=48000, blocksize=480)
        self.assertEqual(spec.count, 480)

    def test_hz_equals_sample_rate_over_blocksize(self):
        spec = audio_neuron_spec(sample_rate=48000, blocksize=480)
        self.assertEqual(spec.hz, 100.0)

    def test_smaller_blocksize_gives_higher_hz(self):
        fast = audio_neuron_spec(sample_rate=48000, blocksize=128)
        slow = audio_neuron_spec(sample_rate=48000, blocksize=4096)
        self.assertGreater(fast.hz, slow.hz)

    def test_rejects_non_positive_blocksize(self):
        with self.assertRaises(ValueError):
            audio_neuron_spec(sample_rate=48000, blocksize=0)
        with self.assertRaises(ValueError):
            audio_neuron_spec(sample_rate=48000, blocksize=-10)


class TestBlocksizeForMaxHz(unittest.TestCase):

    def test_exact_division(self):
        self.assertEqual(blocksize_for_max_hz(sample_rate=48000, max_hz=100.0), 480)

    def test_rounds_up_so_actual_hz_stays_at_or_under_ceiling(self):
        # 48000/33.33.. -> not exact; must round up so actual hz <= max_hz
        bs = blocksize_for_max_hz(sample_rate=48000, max_hz=33.0)
        actual_hz = 48000 / bs
        self.assertLessEqual(actual_hz, 33.0)

    def test_round_trips_with_audio_neuron_spec(self):
        bs = blocksize_for_max_hz(sample_rate=48000, max_hz=20.0)
        spec = audio_neuron_spec(sample_rate=48000, blocksize=bs)
        self.assertLessEqual(spec.hz, 20.0)

    def test_rejects_non_positive_max_hz(self):
        with self.assertRaises(ValueError):
            blocksize_for_max_hz(sample_rate=48000, max_hz=0)


if __name__ == '__main__':
    unittest.main()
