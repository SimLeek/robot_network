"""
tests/test_send_frames_always.py

Tests MenuSubSystem.send_frames_always(): the AI subsystem must receive
the exact same fully-processed (viewport-scaled, menu-composited) frame
a human display would, plus audio -- both were being sent to the
display but only video was reaching the AI, since update_audio was
never called for it at all.

Runs exactly one loop iteration per test by having a mocked call flip
is_running back to False, rather than testing the infinite loop directly.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from robonet.brain.menu_system import MenuSubSystem


class TestSendFramesAlwaysAiAudio(unittest.TestCase):
    """MenuSubSystem's real constructor needs real psk key files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='robonet_send_frames_test_')
        self.psk_path = os.path.join(self.tmpdir, 'psk.key')
        self.server_psk_path = os.path.join(self.tmpdir, 'server_psk.key')
        with open(self.psk_path, 'wb') as f:
            f.write(os.urandom(32))
        with open(self.server_psk_path, 'wb') as f:
            f.write(os.urandom(32))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_settings(self, **overrides):
        vals = {
            'psk_file': self.psk_path, 'server_psk_file': self.server_psk_path,
            'ai_res': [640, 480], 'ai_fps': 30, 'play_audio': False,
            'auto_shutdown_enabled': False,
            'auto_shutdown_no_endpoints_timeout': 30.0,
            'auto_shutdown_idle_timeout': 600.0,
        }
        vals.update(overrides)
        fake = MagicMock()
        fake.__getitem__.side_effect = vals.__getitem__
        return fake

    def _make_menu(self, with_displayer=True, with_ai=True, source_shape=(1080, 1920, 3), audio=None):
        with patch('robonet.brain.menu_system.settings', self._make_settings()):
            menu = MenuSubSystem()
        menu.last_img = np.zeros(source_shape, dtype=np.uint8)
        menu.last_audio = audio
        menu.root = MagicMock()
        menu.root.active_sub = None  # non-desktop path -- exercises the fallback viewport
        menu.root.displayer = MagicMock() if with_displayer else None
        menu.root.ai = MagicMock() if with_ai else None
        menu.is_running = True
        return menu

    def _run_one_iteration(self, menu):
        # Whichever sink is present, flip is_running off after its
        # first call so the loop exits after exactly one pass.
        sink = menu.root.displayer if menu.root.displayer is not None else menu.root.ai

        def stop(*a, **kw):
            menu.is_running = False

        sink.update_frame.side_effect = stop
        asyncio_run(menu.send_frames_always())

    def test_ai_receives_the_same_frame_the_display_does(self):
        menu = self._make_menu(with_displayer=True, with_ai=True)
        self._run_one_iteration(menu)

        display_frame = menu.root.displayer.update_frame.call_args[0][0]
        ai_frame = menu.root.ai.update_frame.call_args[0][0]
        np.testing.assert_array_equal(display_frame, ai_frame)

    def test_ai_receives_audio_when_present(self):
        audio = np.array([0.1, -0.2, 0.3], dtype=np.float32)
        menu = self._make_menu(with_displayer=True, with_ai=True, audio=audio)
        self._run_one_iteration(menu)

        menu.root.ai.update_audio.assert_called_once()
        np.testing.assert_array_equal(menu.root.ai.update_audio.call_args[0][0], audio)

    def test_ai_gets_no_audio_call_when_none_available(self):
        menu = self._make_menu(with_displayer=True, with_ai=True, audio=None)
        self._run_one_iteration(menu)

        menu.root.ai.update_audio.assert_not_called()

    def test_ai_still_works_with_no_display_at_all(self):
        menu = self._make_menu(with_displayer=False, with_ai=True)
        self._run_one_iteration(menu)  # must not raise

        menu.root.ai.update_frame.assert_called_once()

    def test_display_still_works_with_no_ai(self):
        menu = self._make_menu(with_displayer=True, with_ai=False)
        self._run_one_iteration(menu)  # must not raise

        menu.root.displayer.update_frame.assert_called_once()

    def test_both_receive_frames_at_the_configured_display_resolution(self):
        menu = self._make_menu(with_displayer=True, with_ai=True, source_shape=(1080, 1920, 3))
        self._run_one_iteration(menu)

        display_frame = menu.root.displayer.update_frame.call_args[0][0]
        ai_frame = menu.root.ai.update_frame.call_args[0][0]
        self.assertEqual(display_frame.shape[:2], (480, 640))  # ai_res from settings, not the raw source size
        self.assertEqual(ai_frame.shape[:2], (480, 640))


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == '__main__':
    unittest.main()
