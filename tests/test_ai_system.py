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

    def test_update_frame_stores_it(self):
        ai = _ConcreteAISubSystem()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        ai.update_frame(frame)

        self.assertIs(ai.in_img, frame)

    def test_update_audio_stores_it(self):
        ai = _ConcreteAISubSystem()
        audio = np.array([0.1, -0.2, 0.3], dtype=np.float32)

        ai.update_audio(audio)

        self.assertIs(ai.in_aud, audio)

    def test_default_out_mode_is_neuron(self):
        ai = _ConcreteAISubSystem()
        self.assertEqual(ai.out_mode, AISubSystem.OutMode.NEURON)


if __name__ == '__main__':
    unittest.main()
