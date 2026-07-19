"""
tests/test_ai_system.py

Direct tests for AISubSystem. Regression coverage for audio support,
which didn't exist at all -- only update_frame/in_img were present,
despite the human display path receiving both video and audio.
"""

import unittest

import numpy as np

from robonet.brain.ai_system import AISubSystem


class _ConcreteAISubSystem(AISubSystem):
    """Minimal concrete subclass -- AISubSystem itself is abstract
    (start/stop, inherited from SubSystem) and can't be instantiated
    directly, matching its own docstring."""
    def start(self):
        pass

    def stop(self):
        pass


class TestAISubSystem(unittest.TestCase):

    def test_starts_with_no_frame_or_audio(self):
        ai = _ConcreteAISubSystem()
        self.assertIsNone(ai.in_img)
        self.assertIsNone(ai.in_aud)

    def test_starts_with_media_not_ready(self):
        ai = _ConcreteAISubSystem()
        self.assertFalse(ai.has_video)
        self.assertFalse(ai.has_audio)

    def test_update_frame_stores_it(self):
        ai = _ConcreteAISubSystem()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        ai.update_frame(frame)

        self.assertIs(ai.in_img, frame)

    def test_update_frame_sets_has_video(self):
        ai = _ConcreteAISubSystem()
        ai.update_frame(np.zeros((480, 640, 3), dtype=np.uint8))
        self.assertTrue(ai.has_video)

    def test_update_audio_stores_it(self):
        ai = _ConcreteAISubSystem()
        audio = np.array([0.1, -0.2, 0.3], dtype=np.float32)

        ai.update_audio(audio)

        self.assertIs(ai.in_aud, audio)

    def test_update_audio_sets_has_audio(self):
        ai = _ConcreteAISubSystem()
        ai.update_audio(np.array([0.1], dtype=np.float32))
        self.assertTrue(ai.has_audio)

    def test_default_out_mode_is_neuron(self):
        ai = _ConcreteAISubSystem()
        self.assertEqual(ai.out_mode, AISubSystem.OutMode.NEURON)


if __name__ == '__main__':
    unittest.main()


class TestUpdateAudioAcceptsMonoOrStereo(unittest.TestCase):
    """in_aud is documented as mono (N,) or stereo (N, 2) -- neither
    shape gets transformed or rejected here."""

    def test_mono_shape_stored_unchanged(self):
        ai = _ConcreteAISubSystem()
        mono = np.zeros(100, dtype=np.float32)
        ai.update_audio(mono)
        self.assertEqual(ai.in_aud.shape, (100,))

    def test_stereo_shape_stored_unchanged(self):
        ai = _ConcreteAISubSystem()
        stereo = np.zeros((100, 2), dtype=np.float32)
        ai.update_audio(stereo)
        self.assertEqual(ai.in_aud.shape, (100, 2))

    def test_stereo_still_sets_has_audio(self):
        ai = _ConcreteAISubSystem()
        ai.update_audio(np.zeros((100, 2), dtype=np.float32))
        self.assertTrue(ai.has_audio)
