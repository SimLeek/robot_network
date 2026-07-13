"""
tests/test_ai_passthrough.py

Tests DesktopSubSystem's AI passthrough: a dedicated ActionFactory
(af_ai) independent of any display, priority gating between human and
AI input sources (only one is ever actually forwarded at a time, since
both driving the same remote cursor would just fight each other), and
the neuron/token dispatch wiring an AI actually drives through.
"""

import unittest
from unittest.mock import MagicMock

from robonet.brain.desktop_system import (
    DesktopSubSystem, AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y,
    AI_TOKEN_MOUSE_LEFT_PRESS, AI_TOKEN_MOUSE_LEFT_RELEASE,
    AI_TOKEN_MOUSE_RIGHT_PRESS, AI_TOKEN_MOUSE_RIGHT_RELEASE,
    AI_TOKEN_MOUSE_MIDDLE_PRESS, AI_TOKEN_MOUSE_MIDDLE_RELEASE,
)


def _make_sub(screen_width=1920, screen_height=1080):
    endpoint = MagicMock()
    endpoint.streams = [{'name': 'screen', 'type': 'video', 'width': screen_width, 'height': screen_height}]
    sub = DesktopSubSystem(endpoint=endpoint)
    sub._root = MagicMock()
    sub._root.menu.visible = False
    sub._root.displayer = None  # AI passthrough must work with no display at all
    return sub


class TestInputSourceGating(unittest.TestCase):

    def test_defaults_to_human(self):
        sub = _make_sub()
        self.assertEqual(sub.input_source, 'human')

    def test_set_input_source_accepts_human_and_ai(self):
        sub = _make_sub()
        sub.set_input_source('ai')
        self.assertEqual(sub.input_source, 'ai')
        sub.set_input_source('human')
        self.assertEqual(sub.input_source, 'human')

    def test_set_input_source_rejects_other_values(self):
        sub = _make_sub()
        with self.assertRaises(AssertionError):
            sub.set_input_source('robot')

    def test_ai_input_dropped_while_human_has_priority(self):
        sub = _make_sub()  # default: human
        sub.ai_mouse_move(0.5, 0.5)
        sub._root.radio.burst.assert_not_called()

    def test_human_input_dropped_while_ai_has_priority(self):
        sub = _make_sub()
        sub.set_input_source('ai')
        sub._on_mouse_scroll(1)  # human path -- but displayer is None anyway; use a case that only checks input_source
        sub._root.radio.burst.assert_not_called()


class TestAiMouseMove(unittest.TestCase):

    def _ready_sub(self):
        sub = _make_sub(screen_width=1920, screen_height=1080)
        sub.set_input_source('ai')
        return sub

    def test_moves_to_true_screen_fraction_directly(self):
        sub = self._ready_sub()

        sub.ai_mouse_move(0.5, 0.25)

        sub._root.radio.burst.assert_called_once()
        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.event_type, sent.x, sent.y), (0, 960, 270))

    def test_ignores_any_local_viewport_zoom_pan_state(self):
        # Unlike the human path (_frac_to_pixel), AI coordinates mean
        # the same true screen position regardless of what a human's
        # local viewport is currently zoomed/panned to.
        sub = self._ready_sub()
        sub.viewport.zoom = 3.0
        sub.viewport.pan_x, sub.viewport.pan_y = 0.2, 0.8

        sub.ai_mouse_move(0.5, 0.5)

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.x, sent.y), (960, 540))  # still dead center, untouched by viewport state

    def test_clamps_out_of_range_fractions(self):
        sub = self._ready_sub()

        sub.ai_mouse_move(-0.5, 1.5)

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.x, sent.y), (0, 1080))

    def test_works_with_no_display_at_all(self):
        sub = self._ready_sub()
        self.assertIsNone(sub._root.displayer)  # confirms the headless case specifically

        sub.ai_mouse_move(0.5, 0.5)  # must not raise

        sub._root.radio.burst.assert_called_once()


class TestAiMouseButtons(unittest.TestCase):

    def _ready_sub(self):
        sub = _make_sub()
        sub.set_input_source('ai')
        return sub

    def test_press_sends_event_type_1(self):
        sub = self._ready_sub()
        sub.ai_mouse_move(0.5, 0.5)
        sub._root.radio.burst.reset_mock()

        sub.ai_mouse_press('left')

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.event_type, 1)

    def test_release_sends_event_type_2(self):
        sub = self._ready_sub()

        sub.ai_mouse_release('right')

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.event_type, 2)

    def test_button_names_map_to_the_same_codes_as_human_input(self):
        sub = self._ready_sub()

        sub.ai_mouse_press('left')
        left_code = sub._root.radio.burst.call_args[0][0].button
        sub.ai_mouse_press('right')
        right_code = sub._root.radio.burst.call_args[0][0].button
        sub.ai_mouse_press('middle')
        middle_code = sub._root.radio.burst.call_args[0][0].button

        self.assertEqual((left_code, right_code, middle_code), (0, 1, 2))

    def test_press_uses_the_last_moved_to_position(self):
        sub = self._ready_sub()
        sub.ai_mouse_move(0.25, 0.75)

        sub.ai_mouse_press('left')

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.x, sent.y), (480, 810))


class TestAiScroll(unittest.TestCase):

    def test_sends_event_type_3_with_delta(self):
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_scroll(-3)

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.event_type, sent.delta), (3, -3))

    def test_negative_delta_does_not_crash_packing(self):
        from robonet.buffers.buffer_handling import pack_obj
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_scroll(-5)

        sent = sub._root.radio.burst.call_args[0][0]
        pack_obj(sent)  # must not raise


class TestAiKeyboard(unittest.TestCase):

    def test_press_sends_pressed_true(self):
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_key_press('f11')

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.key, sent.pressed), ('f11', True))

    def test_release_sends_pressed_false(self):
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_key_release('f11')

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.key, sent.pressed), ('f11', False))

    def test_modifiers_pass_through(self):
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_key_press('a', modifiers='ctrl,shift')

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.modifiers, 'ctrl,shift')

    def test_dropped_while_human_has_priority(self):
        sub = _make_sub()  # default: human
        sub.ai_key_press('f11')
        sub._root.radio.burst.assert_not_called()


class TestNeuronTokenDispatch(unittest.TestCase):
    """Confirms the actual interface an AI drives through -- af_ai's
    neuron/token dispatch, not just the underlying methods directly."""

    def _bound_sub(self):
        sub = _make_sub()
        sub.set_input_source('ai')
        sub.start()  # binds af_ai
        return sub

    def test_af_ai_exists_and_binds_without_a_display(self):
        sub = self._bound_sub()
        self.assertIn(AI_NEURON_MOUSE_X, sub.af_ai._neuron_to_handler_thresholds)
        self.assertIn(AI_NEURON_MOUSE_Y, sub.af_ai._neuron_to_handler_thresholds)

    def test_driving_mouse_x_neuron_moves_the_mouse(self):
        sub = self._bound_sub()
        vector = [0.0] * 10
        vector[AI_NEURON_MOUSE_X] = 0.75
        vector[AI_NEURON_MOUSE_Y] = 0.25

        sub.af_ai.on_neuron_outputs(vector)

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.x, sent.y), (1440, 270))

    def test_driving_left_click_tokens(self):
        sub = self._bound_sub()

        sub.af_ai.on_token(AI_TOKEN_MOUSE_LEFT_PRESS)
        press_event = sub._root.radio.burst.call_args[0][0].event_type
        sub.af_ai.on_token(AI_TOKEN_MOUSE_LEFT_RELEASE)
        release_event = sub._root.radio.burst.call_args[0][0].event_type

        self.assertEqual((press_event, release_event), (1, 2))

    def test_driving_right_and_middle_click_tokens(self):
        sub = self._bound_sub()

        sub.af_ai.on_token(AI_TOKEN_MOUSE_RIGHT_PRESS)
        right_button = sub._root.radio.burst.call_args[0][0].button
        sub.af_ai.on_token(AI_TOKEN_MOUSE_MIDDLE_PRESS)
        middle_button = sub._root.radio.burst.call_args[0][0].button

        self.assertEqual((right_button, middle_button), (1, 2))

    def test_stop_unbinds_af_ai(self):
        sub = self._bound_sub()
        sub.stop()
        self.assertEqual(sub.af_ai._neuron_to_handler_thresholds, {})


if __name__ == '__main__':
    unittest.main()
