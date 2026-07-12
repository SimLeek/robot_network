"""
tests/test_callback_control.py

Tests the callback-dispatch mechanisms that let something external -- an
AI, or this test standing in for one -- drive a robot or a desktop
"sandbox" through this repo's existing plumbing:

  1. ActionFactory: the generic token/neuron -> handler dispatch used by
     the menu today and meant for AI control generally (bind_ai_token,
     bind_ai_neuron).
  2. RobotHardware.apply_tensor via a SparseVectorBuffer -- the actual
     wire path an AI's sparse control vector takes to reach robot
     hardware.
  3. DesktopHw's KeyEvent/MouseEvent handlers -- the wire path an AI's
     keyboard/mouse commands take to reach a desktop "sandbox".
  4. The brain-side keycode -> pyautogui name mapping those KeyEvents
     are built from before they're even sent.
  5. ServerSystem's shutdown-callback mechanism (register_shutdown_callback
     / shutdown()) -- the hook the auto-shutdown-when-no-endpoints feature
     uses to let embedding code save state (e.g. AI weights) before exit.

(2) and (3) call the real handler methods directly against a minimal
stand-in object rather than constructing full RobotHardware/DesktopHw
instances. Those constructors need real hardware (serial ports,
v4l2loopback, a psk.key file, GStreamer device probing, a live X
session) that isn't available in a plain test environment, and standing
all of that up isn't what "test the callback path" is asking for --
that's an integration/manual-test concern. pyautogui itself is mocked
out for the same reason.

Run: python -m unittest tests.test_callback_control
(or plain `python tests/test_callback_control.py`)
"""

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from robonet.brain.util.action_factory import ActionFactory
from robonet.buffers.buffer_objects import KeyEvent, MouseEvent, SparseVectorBuffer


class TestActionFactoryDispatch(unittest.TestCase):
    """The generic callback dispatch used to give something -- human,
    token stream, or neuron-vector AI output -- control of an action."""

    def test_token_dispatch_calls_bound_handler(self):
        af = ActionFactory()
        called = []
        af.bind_ai_token(lambda: called.append('fired'), token_id=42)

        af.on_token(42)

        self.assertEqual(called, ['fired'])

    def test_token_dispatch_ignores_unbound_token(self):
        af = ActionFactory()
        called = []
        af.bind_ai_token(lambda: called.append('fired'), token_id=42)

        af.on_token(99)  # different token id -- should not fire

        self.assertEqual(called, [])

    def test_neuron_dispatch_respects_threshold(self):
        af = ActionFactory()
        received = []
        af.bind_ai_neuron(lambda v: received.append(v), neuron_index=3, threshold=0.5)

        af.on_neuron_outputs([0.0, 0.0, 0.0, 0.2])  # below threshold -- no fire
        af.on_neuron_outputs([0.0, 0.0, 0.0, 0.9])  # above threshold -- fires

        self.assertEqual(received, [0.9])

    def test_unbind_handler_removes_all_its_bindings(self):
        af = ActionFactory()
        calls = []
        def handler(*_a): calls.append('x')
        af.bind_ai_token(handler, token_id=1)
        af.bind_ai_neuron(handler, neuron_index=0, threshold=0.1)

        af.unbind_handler(handler)
        af.on_token(1)
        af.on_neuron_outputs([1.0])

        self.assertEqual(calls, [])


class _FakeRobotHardware:
    """Minimal RobotHardware stand-in: just enough state for
    RobotHardware._on_spvec to exercise apply_tensor, without
    CamMicSpkRobotHardware's hardware/filesystem dependencies."""
    def __init__(self):
        self.root = MagicMock()
        self.applied = []

    def apply_tensor(self, idx_vec, val_vec):
        self.applied.append((idx_vec.copy(), val_vec.copy()))


class TestRobotControlPath(unittest.TestCase):
    """The wire path a robot's control vector actually takes:
    SparseVectorBuffer -> RobotHardware._on_spvec -> apply_tensor. This is
    the "give an AI control of a robot" path."""

    def test_sparse_vector_reaches_apply_tensor(self):
        from robonet.endpoint.hardware_system import RobotHardware

        hw = _FakeRobotHardware()
        idx = np.array([0, 4], dtype=np.uint32)
        val = np.array([0.75, -0.3], dtype=np.float32)
        buf = SparseVectorBuffer(idx=idx, val=val)

        RobotHardware._on_spvec(hw, 'brain-host', buf)

        self.assertEqual(len(hw.applied), 1)
        applied_idx, applied_val = hw.applied[0]
        np.testing.assert_array_equal(applied_idx, idx)
        np.testing.assert_array_almost_equal(applied_val, val)

    def test_empty_vector_is_a_watchdog_ping_not_a_command(self):
        from robonet.endpoint.hardware_system import RobotHardware

        hw = _FakeRobotHardware()
        buf = SparseVectorBuffer(idx=np.array([], dtype=np.uint32),
                                 val=np.array([], dtype=np.float32))

        RobotHardware._on_spvec(hw, 'brain-host', buf)

        self.assertEqual(hw.applied, [])  # watchdog ping, not forwarded to hardware

    def test_wire_round_trip_then_apply_tensor(self):
        """The full realistic path: pack the buffer like the network would,
        unpack it like the receiving endpoint would, then apply it."""
        from robonet.buffers.buffer_handling import pack_obj, unpack_obj
        from robonet.endpoint.hardware_system import RobotHardware

        hw = _FakeRobotHardware()
        sent = SparseVectorBuffer(idx=np.array([2, 7], dtype=np.uint32),
                                  val=np.array([1.0, -1.0], dtype=np.float32))
        received = unpack_obj(pack_obj(sent))

        RobotHardware._on_spvec(hw, 'brain-host', received)

        applied_idx, applied_val = hw.applied[0]
        np.testing.assert_array_equal(applied_idx, [2, 7])
        np.testing.assert_array_almost_equal(applied_val, [1.0, -1.0])


class _FakeDesktopHw:
    """Minimal DesktopHw stand-in: just the two attributes
    _on_key_event/_on_mouse_event actually touch."""
    def __init__(self):
        self._held_keys = set()
        self._held_buttons = set()


class TestDesktopControlPath(unittest.TestCase):
    """The wire path a desktop 'sandbox' control command takes:
    KeyEvent/MouseEvent -> DesktopHw's handlers -> pyautogui. This is the
    "give an AI control of a sandbox" path."""

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_key_press_and_release_call_pyautogui_and_track_held_state(self, mock_pag):
        from robonet.endpoint.desktop_hardware import DesktopHw

        hw = _FakeDesktopHw()
        DesktopHw._on_key_event(hw, 'brain-host', KeyEvent(key='a', pressed=True))
        self.assertIn('a', hw._held_keys)
        mock_pag.keyDown.assert_called_once_with('a')

        DesktopHw._on_key_event(hw, 'brain-host', KeyEvent(key='a', pressed=False))
        self.assertNotIn('a', hw._held_keys)
        mock_pag.keyUp.assert_called_once_with('a')

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_mouse_move_press_release_scroll(self, mock_pag):
        from robonet.endpoint.desktop_hardware import DesktopHw

        hw = _FakeDesktopHw()
        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=0, x=10, y=20, button=0, delta=0))
        mock_pag.moveTo.assert_called_once_with(10, 20)

        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=1, x=10, y=20, button=1, delta=0))
        self.assertIn('left', hw._held_buttons)
        mock_pag.mouseDown.assert_called_once_with(x=10, y=20, button='left')

        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=2, x=10, y=20, button=1, delta=0))
        self.assertNotIn('left', hw._held_buttons)
        mock_pag.mouseUp.assert_called_once_with(x=10, y=20, button='left')

        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=3, x=0, y=0, button=0, delta=-5))
        mock_pag.scroll.assert_called_once_with(-5)

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_mouse_button_mapping_matches_pyglet_bitmask_values(self, mock_pag):
        # Regression test for the actual bug: pyglet's mouse buttons are
        # bitmask values (LEFT=1, MIDDLE=2, RIGHT=4), not GLFW's
        # sequential 0/1/2 -- confirmed via pyglet source. The old table
        # flipped left and right entirely.
        from robonet.endpoint.desktop_hardware import DesktopHw
        hw = _FakeDesktopHw()

        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=1, x=0, y=0, button=1, delta=0))
        mock_pag.mouseDown.assert_called_with(x=0, y=0, button='left')

        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=1, x=0, y=0, button=4, delta=0))
        mock_pag.mouseDown.assert_called_with(x=0, y=0, button='right')

        DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=1, x=0, y=0, button=2, delta=0))
        mock_pag.mouseDown.assert_called_with(x=0, y=0, button='middle')

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_failsafe_exception_is_caught_not_raised(self, mock_pag):
        """A human yanking the mouse to a corner (pyautogui's built-in
        kill-switch) must not crash the endpoint process."""
        from robonet.endpoint.desktop_hardware import DesktopHw

        class _FailSafe(Exception):
            pass
        mock_pag.FailSafeException = _FailSafe
        mock_pag.moveTo.side_effect = _FailSafe('mouse in corner')

        hw = _FakeDesktopHw()
        try:
            DesktopHw._on_mouse_event(hw, 'brain-host', MouseEvent(event_type=0, x=0, y=0, button=0, delta=0))
        except Exception as e:
            self.fail(f'_on_mouse_event let a FailSafeException escape: {e}')

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_wire_round_trip_then_replay(self, mock_pag):
        """The full realistic path: pack a KeyEvent like the network
        would, unpack it like the endpoint would, then replay it."""
        from robonet.buffers.buffer_handling import pack_obj, unpack_obj
        from robonet.endpoint.desktop_hardware import DesktopHw

        sent = KeyEvent(key='enter', pressed=True, modifiers='ctrl')
        received = unpack_obj(pack_obj(sent))

        hw = _FakeDesktopHw()
        DesktopHw._on_key_event(hw, 'brain-host', received)

        mock_pag.keyDown.assert_called_once_with('enter')
        self.assertIn('enter', hw._held_keys)


class TestKeycodeMapping(unittest.TestCase):
    """The brain-side half of desktop control: raw window-backend keycodes
    -> pyautogui-compatible key names, before a KeyEvent is even sent.
    Built dynamically from whatever the actual backend's keys object
    reports -- these differ enormously between backends (pyglet's F4 is
    0xffc1, GLFW's is 293), so _FakeWKeys uses pyglet-like values here
    specifically to prove that, not GLFW ones."""

    def test_printable_ascii_passthrough_needs_no_keys_object(self):
        from robonet.brain.desktop_system import keycode_to_pyautogui
        self.assertEqual(keycode_to_pyautogui(ord('5')), '5')
        self.assertEqual(keycode_to_pyautogui(ord('A')), 'a')  # GLFW/pyglet both report letters uppercase

    def test_named_keys_resolve_via_the_actual_backends_values(self):
        from robonet.brain.desktop_system import keycode_to_pyautogui
        self.assertEqual(keycode_to_pyautogui(0xff0d, _FakeWKeys), 'enter')
        self.assertEqual(keycode_to_pyautogui(0xffc1, _FakeWKeys), 'f4')
        self.assertEqual(keycode_to_pyautogui(0xffe1, _FakeWKeys), 'shiftleft')

    def test_glfw_style_number_does_not_falsely_match_a_pyglet_backend(self):
        # Regression check for the actual bug: 293 is GLFW's F4, but
        # under a pyglet-style keys object (_FakeWKeys) it must NOT
        # resolve to 'f4' just because it happens to be a hardcoded
        # value from a different backend's convention.
        from robonet.brain.desktop_system import keycode_to_pyautogui
        self.assertIsNone(keycode_to_pyautogui(293, _FakeWKeys))

    def test_unmapped_key_returns_none(self):
        from robonet.brain.desktop_system import keycode_to_pyautogui
        self.assertIsNone(keycode_to_pyautogui(999999, _FakeWKeys))

    def test_no_keys_object_still_resolves_ascii_but_not_specials(self):
        from robonet.brain.desktop_system import keycode_to_pyautogui
        self.assertEqual(keycode_to_pyautogui(ord('q')), 'q')
        self.assertIsNone(keycode_to_pyautogui(0xffc1))  # F4, unresolvable without a keys object


def _stub_out_missing_displayarray_font_submodule():
    """robonet.brain.main_system imports DisplaySubSystem, which pulls in
    displayarray.input_mgl -> displayarray.font.get_texture_atlas. That
    submodule doesn't exist in the currently-installed displayarray build
    in every environment (it's on an actively-developed branch) -- stub
    it out here since main_system's own code never touches font rendering
    at all; this only exists to make ServerSystem importable for the test
    below, not to test displayarray itself."""
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


class TestShutdownCallbacks(unittest.TestCase):
    """The auto-shutdown-when-no-endpoints feature needs callbacks to
    actually run before the process exits (e.g. to save AI weights) --
    this is that mechanism, ServerSystem.register_shutdown_callback /
    .shutdown()."""

    @classmethod
    def setUpClass(cls):
        _stub_out_missing_displayarray_font_submodule()

    def _make_server_system(self):
        from robonet.brain.main_system import ServerSystem
        return ServerSystem(MagicMock(), MagicMock(), displayer=MagicMock())

    def test_callbacks_run_in_order_then_raises_systemexit(self):
        sm = self._make_server_system()
        order = []
        sm.register_shutdown_callback(lambda: order.append('first'))
        sm.register_shutdown_callback(lambda: order.append('second'))

        with self.assertRaises(SystemExit):
            sm.shutdown(reason='test')

        self.assertEqual(order, ['first', 'second'])

    def test_a_failing_callback_does_not_block_the_rest(self):
        sm = self._make_server_system()
        order = []
        def boom(): raise RuntimeError('a bad callback')
        sm.register_shutdown_callback(lambda: order.append('before'))
        sm.register_shutdown_callback(boom)
        sm.register_shutdown_callback(lambda: order.append('after'))

        with self.assertRaises(SystemExit):
            sm.shutdown(reason='test')

        self.assertEqual(order, ['before', 'after'])

    def test_shutdown_is_idempotent(self):
        sm = self._make_server_system()
        order = []
        sm.register_shutdown_callback(lambda: order.append('ran'))

        with self.assertRaises(SystemExit):
            sm.shutdown(reason='first call')
        # A second call (e.g. two watchers firing close together) must not
        # re-run callbacks or raise again.
        sm.shutdown(reason='second call')

        self.assertEqual(order, ['ran'])


class _FakeModifiers:
    def __init__(self, ctrl=False, shift=False, alt=False):
        self.ctrl = ctrl
        self.shift = shift
        self.alt = alt


class _FakeWKeys:
    ACTION_PRESS = 'PRESS'
    ACTION_RELEASE = 'RELEASE'
    ACTION_REPEAT = 'REPEAT'
    # Deliberately pyglet-like (0xff-prefixed) rather than GLFW-style
    # small integers, to prove the mapping is actually backend-agnostic
    # rather than secretly assuming GLFW numbering.
    ENTER = 0xff0d
    F4 = 0xffc1
    LEFT_SHIFT = 0xffe1


def _make_fake_root(with_displayer=True, menu_visible=False):
    root = MagicMock()
    root.menu.visible = menu_visible
    if with_displayer:
        root.displayer.displayer.displayer.config.wnd.keys = _FakeWKeys
    else:
        root.displayer = None
    return root


class TestDesktopSubSystemLifecycle(unittest.TestCase):

    def test_init_state(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        ep = MagicMock()

        sub = DesktopSubSystem(endpoint=ep)

        self.assertIs(sub._endpoint, ep)
        self.assertIsNone(sub._root)
        self.assertFalse(sub._running)
        self.assertFalse(sub._bound)
        self.assertEqual(sub.handlers, {})

    def test_setup_stores_root(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        root = MagicMock()

        sub.setup(root)

        self.assertIs(sub._root, root)

    def test_start_binds_input_and_sets_running(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        sub._root = _make_fake_root()

        sub.start()

        self.assertTrue(sub._running)
        self.assertTrue(sub._bound)
        sub._root.displayer.af_thru.bind_keyboard.assert_called_once_with(sub._on_keyboard)
        sub._root.displayer.af_thru.bind_mouse_move.assert_called_once_with(sub._on_mouse_move)
        sub._root.displayer.af_thru.bind_mouse_press.assert_called_once_with(sub._on_mouse_press)
        sub._root.displayer.af_thru.bind_mouse_release.assert_called_once_with(sub._on_mouse_release)
        sub._root.displayer.af_thru.bind_mouse_scroll.assert_called_once_with(sub._on_mouse_scroll)

    def test_bind_input_noop_for_ai_driven_session(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        sub._root = _make_fake_root(with_displayer=False)

        sub.start()

        self.assertFalse(sub._bound)  # nothing to bind to yet

    def test_stop_unbinds_and_clears_running(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        sub._root = _make_fake_root()
        sub.start()

        sub.stop()

        self.assertFalse(sub._running)
        self.assertFalse(sub._bound)
        sub._root.displayer.af_thru.unbind_keyboard.assert_called_once()
        sub._root.displayer.af_thru.unbind_mouse_move.assert_called_once()
        sub._root.displayer.af_thru.unbind_mouse_press.assert_called_once()
        sub._root.displayer.af_thru.unbind_mouse_release.assert_called_once()
        sub._root.displayer.af_thru.unbind_mouse_scroll.assert_called_once()

    def test_unbind_when_never_bound_is_a_noop(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        sub._root = _make_fake_root()

        sub.stop()  # never called start() -- must not raise or call unbind

        sub._root.displayer.af_thru.unbind_keyboard.assert_not_called()

    def test_async_loops_is_empty(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        self.assertEqual(sub.async_loops(MagicMock()), [])


class TestDesktopSubSystemKeyboardForwarding(unittest.TestCase):

    def _make_sub(self, **root_kwargs):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = DesktopSubSystem(endpoint=MagicMock())
        sub._root = _make_fake_root(**root_kwargs)
        return sub

    def test_press_sends_key_event_pressed_true(self):
        sub = self._make_sub()

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_PRESS, _FakeModifiers())

        sub._root.radio.burst.assert_called_once()
        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.key, 'a')
        self.assertTrue(sent.pressed)

    def test_release_sends_key_event_pressed_false(self):
        sub = self._make_sub()

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_RELEASE, _FakeModifiers())

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertFalse(sent.pressed)

    def test_repeat_action_is_ignored(self):
        sub = self._make_sub()

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_REPEAT, _FakeModifiers())

        sub._root.radio.burst.assert_not_called()

    def test_unmapped_keycode_is_dropped(self):
        sub = self._make_sub()

        sub._on_keyboard(999999, _FakeWKeys.ACTION_PRESS, _FakeModifiers())

        sub._root.radio.burst.assert_not_called()

    def test_modifiers_encoded_correctly(self):
        sub = self._make_sub()

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_PRESS, _FakeModifiers(ctrl=True, shift=True))

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.modifiers, 'ctrl,shift')

    def test_no_modifiers_gives_empty_string(self):
        sub = self._make_sub()

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_PRESS, _FakeModifiers())

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.modifiers, '')

    def test_suppressed_while_menu_open(self):
        sub = self._make_sub(menu_visible=True)

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_PRESS, _FakeModifiers())

        sub._root.radio.burst.assert_not_called()

    def test_suppressed_for_ai_driven_session(self):
        sub = self._make_sub(with_displayer=False)

        sub._on_keyboard(ord('a'), _FakeWKeys.ACTION_PRESS, _FakeModifiers())

        sub._root.radio.burst.assert_not_called()


class TestDesktopSubSystemMouseForwarding(unittest.TestCase):

    def _make_sub(self, screen_width=1920, screen_height=1080, **root_kwargs):
        from robonet.brain.desktop_system import DesktopSubSystem
        endpoint = MagicMock()
        endpoint.streams = [{'name': 'screen', 'type': 'video',
                             'width': screen_width, 'height': screen_height}]
        sub = DesktopSubSystem(endpoint=endpoint)
        sub._root = _make_fake_root(**root_kwargs)
        return sub

    def test_move_scales_normalized_position_by_screen_resolution(self):
        sub = self._make_sub(screen_width=1920, screen_height=1080)

        sub._on_mouse_move(0.5, 0.25)  # raw tx, ty -- swapped internally, then scaled

        sub._root.radio.burst.assert_called_once()
        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.event_type, sent.x, sent.y), (0, 480, 540))

    def test_move_uses_default_resolution_when_endpoint_reports_no_screen_stream(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        endpoint = MagicMock()
        endpoint.streams = [{'name': 'mic', 'type': 'audio'}]  # no 'screen' entry
        sub = DesktopSubSystem(endpoint=endpoint)
        sub._root = _make_fake_root()

        sub._on_mouse_move(1.0, 1.0)  # far corner

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.x, sent.y), (1920, 1080))  # falls back to the 1920x1080 default

    def test_move_suppressed_while_menu_open(self):
        sub = self._make_sub(menu_visible=True)
        sub._on_mouse_move(0.1, 0.2)
        sub._root.radio.burst.assert_not_called()

    def test_press_sends_event_type_1(self):
        sub = self._make_sub(screen_width=1920, screen_height=1080)

        sub._on_mouse_press(0.5, 0.25, 0)

        sub._root.radio.burst.assert_called_once()
        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.event_type, sent.x, sent.y), (1, 480, 540))

    def test_release_sends_event_type_2(self):
        sub = self._make_sub(screen_width=1920, screen_height=1080)

        sub._on_mouse_release(0.5, 0.25, 0)

        sub._root.radio.burst.assert_called_once()
        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual((sent.event_type, sent.x, sent.y), (2, 480, 540))

    def test_press_suppressed_while_menu_open(self):
        sub = self._make_sub(menu_visible=True)
        sub._on_mouse_press(0.1, 0.2, 0)
        sub._root.radio.burst.assert_not_called()

    def test_release_suppressed_while_menu_open(self):
        sub = self._make_sub(menu_visible=True)
        sub._on_mouse_release(0.1, 0.2, 0)
        sub._root.radio.burst.assert_not_called()

    def test_scroll_sends_event_type_3_with_delta(self):
        sub = self._make_sub()

        sub._on_mouse_scroll(-3)

        sent = sub._root.radio.burst.call_args[0][0]
        self.assertEqual(sent.event_type, 3)
        self.assertEqual(sent.delta, -3)

    def test_scroll_suppressed_for_ai_driven_session(self):
        sub = self._make_sub(with_displayer=False)
        sub._on_mouse_scroll(-3)
        sub._root.radio.burst.assert_not_called()


if __name__ == '__main__':
    unittest.main()
