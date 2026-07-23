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
    """A subclass with its own no-op start/stop, for existing tests
    that predate AISubSystem itself becoming concrete (it gained real
    start/stop to manage an attached bridge's lifecycle, which
    incidentally satisfies SubSystem's abstract methods -- AISubSystem()
    directly works fine now too, see TestBridgeLifecycle below)."""
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


class TestAISubSystemIsConcrete(unittest.TestCase):
    """Real start()/stop() (managing an attached bridge) incidentally
    satisfies SubSystem's abstract methods -- AISubSystem itself is
    now directly instantiable as a "no in-process AI, just a bridge
    host" configuration."""

    def test_instantiates_directly_with_no_subclass(self):
        ai = AISubSystem()  # must not raise
        self.assertIsNone(ai.bridge)

    def test_setup_stores_root(self):
        ai = AISubSystem()
        root = object()
        ai.setup(root)
        self.assertIs(ai._root, root)

    def test_async_loops_default_is_empty(self):
        ai = AISubSystem()
        self.assertEqual(ai.async_loops(None), [])


class TestBridgeLifecycle(unittest.TestCase):
    """AISubSystem owns bridge start/stop now, not ServerSystem --
    this is the layer that actually needs to test it."""

    def test_start_calls_bridge_start_when_attached(self):
        from unittest.mock import MagicMock
        ai = AISubSystem()
        ai.bridge = MagicMock()
        ai.start()
        ai.bridge.start.assert_called_once()

    def test_stop_calls_bridge_stop_when_attached(self):
        from unittest.mock import MagicMock
        ai = AISubSystem()
        ai.bridge = MagicMock()
        ai.stop()
        ai.bridge.stop.assert_called_once()

    def test_no_bridge_does_not_crash_start_or_stop(self):
        ai = AISubSystem()
        ai.start()  # must not raise
        ai.stop()   # must not raise

    def test_subclass_must_call_super_to_get_bridge_lifecycle(self):
        # Documents the contract stated in AISubSystem.start()'s
        # docstring: a subclass overriding start() without calling
        # super().start() will NOT get its bridge started.
        from unittest.mock import MagicMock
        ai = _ConcreteAISubSystem()  # overrides start()/stop() as bare no-ops
        ai.bridge = MagicMock()
        ai.start()
        ai.bridge.start.assert_not_called()


class TestUpdateSelectionText(unittest.TestCase):

    def test_stores_locally_for_an_in_process_ai(self):
        ai = AISubSystem()
        ai.update_selection_text('hello from xsel')
        self.assertEqual(ai.selection_text, 'hello from xsel')
        self.assertTrue(ai.has_selection_text)

    def test_forwards_through_the_bridge_when_attached(self):
        from unittest.mock import MagicMock
        ai = AISubSystem()
        ai.bridge = MagicMock()
        ai.update_selection_text('some text')
        ai.bridge.send.assert_called_once_with({'event': 'selection_text', 'text': 'some text'})

    def test_does_not_raise_with_no_bridge_attached(self):
        ai = AISubSystem()
        ai.update_selection_text('some text')  # must not raise


class TestUpdateFrameAndAudioForwardThroughTheBridge(unittest.TestCase):
    """update_frame/update_audio now match update_selection_text's own
    shape: store locally for an in-process AI, forward through the
    bridge if one's attached -- via the shared memory channels
    (write_channel), not the control connection, since video/audio is
    exactly the high-frequency/high-bandwidth data those channels
    exist for."""

    def test_update_frame_still_sets_in_img_and_has_video(self):
        ai = AISubSystem()
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        ai.update_frame(frame)
        self.assertIs(ai.in_img, frame)
        self.assertTrue(ai.has_video)

    def test_update_audio_still_sets_in_aud_and_has_audio(self):
        ai = AISubSystem()
        audio = np.zeros(100, dtype=np.float32)
        ai.update_audio(audio)
        self.assertIs(ai.in_aud, audio)
        self.assertTrue(ai.has_audio)

    def test_no_bridge_does_not_raise_on_update_frame_or_audio(self):
        ai = AISubSystem()
        ai.update_frame(np.zeros((4, 4, 3), dtype=np.uint8))  # must not raise
        ai.update_audio(np.zeros(100, dtype=np.float32))       # must not raise

    def test_update_frame_forwards_bytes_through_the_bridge(self):
        from unittest.mock import MagicMock
        ai = AISubSystem()
        ai.bridge = MagicMock()
        frame = np.zeros((4, 4, 3), dtype=np.uint8)

        ai.update_frame(frame)

        ai.bridge.write_channel.assert_called_once_with('video_frame', 'video', frame.tobytes())

    def test_update_audio_forwards_bytes_through_the_bridge(self):
        from unittest.mock import MagicMock
        ai = AISubSystem()
        ai.bridge = MagicMock()
        audio = np.zeros(100, dtype=np.float32)

        ai.update_audio(audio)

        ai.bridge.write_channel.assert_called_once_with('audio_chunk', 'audio', audio.tobytes())

    def test_real_bridge_round_trips_a_real_frame_correctly(self):
        from robonet.bridge.bridge_control import BridgeServer
        ai = AISubSystem()
        ai.bridge = BridgeServer()
        try:
            frame = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
            ai.update_frame(frame)
            raw = ai.bridge.channels['video_frame'].read()
            reconstructed = np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 3)
            np.testing.assert_array_equal(frame, reconstructed)
        finally:
            ai.bridge.stop()

    def test_real_bridge_round_trips_real_audio_correctly(self):
        from robonet.bridge.bridge_control import BridgeServer
        ai = AISubSystem()
        ai.bridge = BridgeServer()
        try:
            audio = np.random.uniform(-1, 1, 4800).astype(np.float32)
            ai.update_audio(audio)
            raw = ai.bridge.channels['audio_chunk'].read()
            reconstructed = np.frombuffer(raw, dtype=np.float32)
            np.testing.assert_array_equal(audio, reconstructed)
        finally:
            ai.bridge.stop()
