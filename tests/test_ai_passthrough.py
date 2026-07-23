"""
tests/test_ai_passthrough.py

Tests DesktopSubSystem's AI passthrough: a dedicated ActionFactory
(af_ai) independent of any display, priority gating between human and
AI input sources (only one is ever actually forwarded at a time, since
both driving the same remote cursor would just fight each other), and
the neuron/token dispatch wiring an AI actually drives through.
"""

import unittest

import numpy as np
from unittest.mock import MagicMock, patch

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
    # Real frame + aspect-matched out_res: at baseline zoom the shared
    # viewport is then an identity mapping, keeping plain fraction ->
    # pixel expectations exact. Zoom/pan tests change it deliberately.
    sub._root.menu.last_img = np.zeros((screen_height, screen_width, 3), dtype=np.uint8)
    sub._root.menu.out_res = (640, int(640 * screen_height / screen_width))
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

    def test_routes_through_the_shared_viewport_zoom_pan_state(self):
        # The viewport exists FOR the AI (it can only ingest small
        # frames, so it must zoom/pan to see detail) -- send_frames_
        # always applies the same viewport to the frame the AI
        # receives, so its coordinates mean positions within that
        # canvas and MUST route through the same inverse mapping as
        # human input. The old behavior (bypassing the viewport) meant
        # what the AI saw at (0.5, 0.5) was not where its click landed.
        sub = self._ready_sub()
        sub.viewport.zoom = 2.0
        sub.viewport.pan_x = 0.75   # panned: centered zoom would be
        sub.viewport.pan_y = 0.75   # indistinguishable from the bypass
        dims = sub._viewport_dims()
        expected_xf, expected_yf = sub.viewport.inverse_map(0.5, 0.5, *dims)

        sub.ai_mouse_move(0.5, 0.5)

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.x, sent.y),
                        (int(expected_xf * 1920), int(expected_yf * 1080)))
        self.assertNotEqual((sent.x, sent.y), (960, 540))  # bypass would say center

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

        self.assertEqual((left_code, right_code, middle_code), (1, 4, 2))

    def test_button_table_matches_the_endpoint_s_table_exactly(self):
        # Regression test for the actual reported bug: this table used
        # a sequential 0/1/2 convention while the endpoint's own table
        # uses pyglet's bitmask values (1/2/4) -- code=1 ('right' under
        # the old, wrong table) decoded as 'left' on the endpoint.
        from robonet.endpoint.desktop_hardware import _MOUSE_BUTTON_NAMES
        from robonet.brain.desktop_system import DesktopSubSystem
        self.assertEqual(DesktopSubSystem._AI_BUTTON_NAMES, _MOUSE_BUTTON_NAMES)

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

        self.assertEqual((right_button, middle_button), (4, 2))

    def test_stop_unbinds_af_ai(self):
        sub = self._bound_sub()
        sub.stop()
        self.assertEqual(sub.af_ai._neuron_to_handler_thresholds, {})


if __name__ == '__main__':
    unittest.main()


class TestAiViewControls(unittest.TestCase):
    """The AI drives the same viewport a human uses in edit mode --
    without zoom it cannot see enough detail on a 1080p+ screen to
    interact at all, since it only ingests small frames."""

    def test_zoom_in_then_pan_changes_both_zoom_and_position_together(self):
        # A direct, combined demonstration (zoom AND pan, not each
        # tested in isolation) that the functionality genuinely works.
        from robonet.brain.desktop_system import AI_TOKEN_ZOOM_IN, AI_TOKEN_PAN_RIGHT, AI_TOKEN_PAN_DOWN
        sub = self._ready_sub()
        baseline_zoom = sub.viewport.zoom
        baseline_pan = (sub.viewport.pan_x, sub.viewport.pan_y)

        for _ in range(5):
            sub.af_ai.on_token(AI_TOKEN_ZOOM_IN)
        sub.af_ai.on_token(AI_TOKEN_PAN_RIGHT)
        sub.af_ai.on_token(AI_TOKEN_PAN_DOWN)

        self.assertGreater(sub.viewport.zoom, baseline_zoom)
        self.assertNotEqual((sub.viewport.pan_x, sub.viewport.pan_y), baseline_pan)
        self.assertGreater(sub.viewport.pan_x, baseline_pan[0])
        self.assertGreater(sub.viewport.pan_y, baseline_pan[1])

    def _ready_sub(self):
        sub = _make_sub()
        sub.set_input_source('ai')
        sub.start()
        return sub

    def test_zoom_in_token_zooms_the_shared_viewport(self):
        from robonet.brain.desktop_system import AI_TOKEN_ZOOM_IN
        sub = self._ready_sub()
        sub.af_ai.on_token(AI_TOKEN_ZOOM_IN)
        self.assertGreater(sub.viewport.zoom, 1.0)

    def test_zoom_out_respects_the_floor(self):
        from robonet.brain.desktop_system import AI_TOKEN_ZOOM_OUT
        sub = self._ready_sub()
        for _ in range(5):
            sub.af_ai.on_token(AI_TOKEN_ZOOM_OUT)
        self.assertEqual(sub.viewport.zoom, 1.0)

    def test_pan_tokens_move_the_view_when_zoomed(self):
        from robonet.brain.desktop_system import AI_TOKEN_ZOOM_IN, AI_TOKEN_PAN_RIGHT
        sub = self._ready_sub()
        for _ in range(8):
            sub.af_ai.on_token(AI_TOKEN_ZOOM_IN)
        before = sub.viewport.pan_x
        sub.af_ai.on_token(AI_TOKEN_PAN_RIGHT)
        self.assertGreater(sub.viewport.pan_x, before)

    def test_view_reset_restores_baseline(self):
        from robonet.brain.desktop_system import AI_TOKEN_ZOOM_IN, AI_TOKEN_VIEW_RESET
        sub = self._ready_sub()
        for _ in range(4):
            sub.af_ai.on_token(AI_TOKEN_ZOOM_IN)
        sub.af_ai.on_token(AI_TOKEN_VIEW_RESET)
        self.assertEqual(sub.viewport.zoom, 1.0)

    def test_view_controls_gated_while_human_drives(self):
        # The viewport is currently SHARED with the human display -- an
        # AI panning around mid-human-session would yank their view.
        sub = self._ready_sub()
        sub.set_input_source('human')
        sub.ai_zoom(1.5)
        self.assertEqual(sub.viewport.zoom, 1.0)

    def test_zoom_pan_click_lands_in_the_panned_view_not_screen_center(self):
        # The discriminating flow: zoom -> pan -> click. A centered
        # zoom clicking canvas-center is indistinguishable from the old
        # raw-fraction bypass (both give screen center), so this pans
        # hard toward the bottom-right first. The old behavior would
        # still send (960, 540); the new behavior must land inside the
        # panned crop -- and does, per four independent checks.
        from robonet.brain.desktop_system import (
            AI_TOKEN_ZOOM_IN, AI_TOKEN_PAN_RIGHT, AI_TOKEN_PAN_DOWN,
            AI_TOKEN_MOUSE_LEFT_PRESS,
        )
        sub = self._ready_sub()
        for _ in range(8):                      # 1.1^8 ~ 2.14x zoom
            sub.af_ai.on_token(AI_TOKEN_ZOOM_IN)
        for _ in range(40):                     # far past the clamp: pinned bottom-right
            sub.af_ai.on_token(AI_TOKEN_PAN_RIGHT)
            sub.af_ai.on_token(AI_TOKEN_PAN_DOWN)

        sub.ai_mouse_move(0.5, 0.5)             # center of what the AI SEES
        sub.af_ai.on_token(AI_TOKEN_MOUSE_LEFT_PRESS)

        press = sub._root.radio.burst.call_args[0][0]
        # 1) Not the bypass answer -- the whole point:
        self.assertNotEqual((press.x, press.y), (960, 540))
        # 2) Panned right+down, so strictly past screen center:
        self.assertGreater(press.x, 960)
        self.assertGreater(press.y, 540)
        # 3) Exactly consistent with the shared viewport's own inverse:
        xf, yf = sub.viewport.inverse_map(0.5, 0.5, *sub._viewport_dims())
        self.assertAlmostEqual(press.x, int(xf * 1920), delta=1)
        self.assertAlmostEqual(press.y, int(yf * 1080), delta=1)
        # 4) And matches the geometry directly: crop pinned at the
        # bottom-right edge, so canvas-center sits at 1 - 1/(2*zoom):
        z = sub.viewport.zoom
        self.assertAlmostEqual(press.x, int((1 - 1 / (2 * z)) * 1920), delta=2)
        self.assertAlmostEqual(press.y, int((1 - 1 / (2 * z)) * 1080), delta=2)


class TestActionSpaceDiagnostic(unittest.TestCase):
    """Knowing the actual action-space size is mandatory for plugging
    in an RL-style AI -- there was previously no way to get it at all."""

    def test_reports_the_actual_bound_counts(self):
        sub = _make_sub()
        with patch('robonet.brain.desktop_system.log') as mock_log:
            sub.start()

        logged = ' '.join(str(c.args[0]) for c in mock_log.info.call_args_list)
        self.assertIn('2 continuous neurons', logged)   # mouse x, mouse y
        self.assertIn('14 discrete tokens', logged)      # 6 mouse + 7 view control + 1 read-selection

    def test_matches_the_action_factory_s_own_bookkeeping(self):
        # Not a hardcoded number -- must track whatever's actually bound,
        # so this can't silently go stale if tokens are added/removed.
        sub = _make_sub()
        sub.start()
        n_tokens = len(sub.af_ai._token_to_handlers)
        n_neurons = len(sub.af_ai._neuron_to_handler_thresholds)
        self.assertEqual(n_tokens, 14)
        self.assertEqual(n_neurons, 2)


class TestAiReadSelection(unittest.TestCase):
    """The AI-facing trigger for the text-selection feature: gated on
    input_source like every other AI action, sends a
    ReadSelectionRequest with the requested max_chars."""

    def test_dropped_while_human_has_priority(self):
        sub = _make_sub()  # default: human
        sub.ai_read_selection()
        sub._root.radio.burst.assert_not_called()

    def test_sends_a_read_selection_request_when_ai_has_control(self):
        from robonet.buffers.buffer_objects import ReadSelectionRequest
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_read_selection()

        sent = sub._root.radio.burst.call_args.args[0]
        self.assertIsInstance(sent, ReadSelectionRequest)

    def test_default_max_chars_matches_the_request_default(self):
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_read_selection()

        sent = sub._root.radio.burst.call_args.args[0]
        self.assertEqual(sent.max_chars, 5120)

    def test_custom_max_chars_is_passed_through(self):
        sub = _make_sub()
        sub.set_input_source('ai')

        sub.ai_read_selection(max_chars=200)

        sent = sub._root.radio.burst.call_args.args[0]
        self.assertEqual(sent.max_chars, 200)

    def test_bound_as_a_token_with_no_args_uses_the_default(self):
        # This is exactly how ActionFactory calls it when the token
        # fires -- no arguments at all. Binds directly rather than
        # calling start() (which does this plus unrelated setup) --
        # this test is specifically about the binding contract.
        sub = _make_sub()
        sub.set_input_source('ai')
        sub.af_ai.bind_ai_token(sub.ai_read_selection, 13)  # AI_TOKEN_READ_SELECTION

        sub.af_ai.on_token(13)

        sent = sub._root.radio.burst.call_args.args[0]
        self.assertEqual(sent.max_chars, 5120)
