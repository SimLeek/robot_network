"""
tests/test_action_factory.py

Regression test: unbind_all() cleared every handler except
mouse_press/mouse_release, leaving stale ones bound. In practice
_bind_input() re-binds these on every connect anyway, which masked the
gap for that specific path, but unbind_all() should actually unbind
everything it claims to.
"""

import unittest
from unittest.mock import MagicMock

from robonet.brain.util.action_factory import ActionFactory


class TestUnbindAll(unittest.TestCase):

    def test_clears_mouse_press_handler(self):
        af = ActionFactory()
        af.bind_mouse_press(MagicMock())
        af.unbind_all()
        af.on_mouse_press(0, 0, 0)  # must not raise, and must not call the stale handler
        self.assertIsNone(af._mouse_press_handler)

    def test_clears_mouse_release_handler(self):
        af = ActionFactory()
        af.bind_mouse_release(MagicMock())
        af.unbind_all()
        self.assertIsNone(af._mouse_release_handler)

    def test_clears_every_handler_type(self):
        af = ActionFactory()
        af.bind_keyboard(MagicMock())
        af.bind_mouse_move(MagicMock())
        af.bind_mouse_click(MagicMock())
        af.bind_mouse_press(MagicMock())
        af.bind_mouse_release(MagicMock())
        af.bind_mouse_scroll(MagicMock())
        af.bind_key(MagicMock(), key_code=65)
        af.bind_ai_token(MagicMock(), token_id=1)
        af.bind_ai_neuron(MagicMock(), neuron_index=1)

        af.unbind_all()

        self.assertEqual(af._keyboard_handler, [])
        self.assertIsNone(af._mouse_move_handler)
        self.assertIsNone(af._mouse_click_handler)
        self.assertIsNone(af._mouse_press_handler)
        self.assertIsNone(af._mouse_release_handler)
        self.assertIsNone(af._mouse_scroll_handler)
        self.assertEqual(af._key_to_handlers, {})
        self.assertEqual(af._token_to_handlers, {})
        self.assertEqual(af._neuron_to_handler_thresholds, {})

    def test_rebinding_after_unbind_all_works(self):
        af = ActionFactory()
        stale = MagicMock()
        fresh = MagicMock()
        af.bind_mouse_press(stale)
        af.unbind_all()
        af.bind_mouse_press(fresh)

        af.on_mouse_press(1, 2, 0)

        stale.assert_not_called()
        fresh.assert_called_once_with(1, 2, 0)


if __name__ == '__main__':
    unittest.main()
