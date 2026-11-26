"""
tests/test_ai_bridge_actions.py

The AI -> robonet direction of the bridge (want_control, key/mouse
actions, release on AI loss), channel re-announce after a channel is
created late, and the endpoint's blocked key combos.
"""

import time
import unittest
import uuid
from unittest.mock import MagicMock

from robonet.brain.ai_system import AISubSystem
from robonet.brain.desktop_system import DesktopSubSystem
from robonet.bridge.ai_client_base import RobonetAIClient
from robonet.bridge.bridge_control import BridgeServer
from robonet.buffers.buffer_objects import KeyEvent, MouseEvent
from robonet.endpoint.desktop_hardware import completes_blocked_combo
from robonet.killswitch import KILL_COMBO


class TestBlockedCombos(unittest.TestCase):
    def test_single_key_combo_blocks_that_key(self):
        self.assertTrue(completes_blocked_combo('f11', set(), '', [['f11']]))

    def test_other_keys_pass(self):
        self.assertFalse(completes_blocked_combo('w', set(), '', [['f11']]))

    def test_multi_key_combo_needs_the_rest_held(self):
        combos = [['alt', 'f4']]
        self.assertFalse(completes_blocked_combo('f4', set(), '', combos))
        self.assertTrue(completes_blocked_combo('f4', {'altleft'}, '', combos))

    def test_modifiers_string_counts_as_held(self):
        self.assertTrue(completes_blocked_combo('f4', set(), 'ctrl,alt', [['alt', 'f4']]))

    def test_left_right_variants_collapse(self):
        self.assertTrue(completes_blocked_combo('t', {'ctrlright', 'shiftleft'}, '', [['ctrl', 'shift', 't']]))

    def test_kill_combo_is_blocked_when_completed_by_any_key(self):
        combos = [list(KILL_COMBO)]
        self.assertTrue(completes_blocked_combo('f12', {'ctrlleft', 'shiftleft'}, 'alt', combos))
        self.assertTrue(completes_blocked_combo('shiftleft', {'ctrlleft', 'f12'}, 'alt', combos))
        self.assertFalse(completes_blocked_combo('f12', {'ctrlleft', 'shiftleft'}, '', combos))


def _ai_with_desktop():
    root = MagicMock()
    root.menu.last_img = None
    desk = DesktopSubSystem.__new__(DesktopSubSystem)
    DesktopSubSystem.__init__(desk, MagicMock(streams=[], hostname='bee', ip='127.0.0.1'))
    desk._root = root
    root.active_sub = desk
    ai = AISubSystem()
    ai.setup(root)
    ai.bridge = MagicMock(connected=True)
    ai.bridge.ai_is_alive.return_value = True
    return ai, desk, root


def _bursts(root):
    return [c.args[0] for c in root.radio.burst.call_args_list]


class TestAIActions(unittest.TestCase):
    def test_actions_dropped_until_ai_has_control(self):
        ai, desk, root = _ai_with_desktop()
        ai.handle_bridge_message({'event': 'key', 'key': 'w', 'pressed': True})
        self.assertEqual(_bursts(root), [])
        self.assertEqual(desk._held_keys_brain, {})  # not refreshed later either

    def test_want_control_then_key_reaches_radio(self):
        ai, desk, root = _ai_with_desktop()
        ai.handle_bridge_message({'event': 'want_control', 'value': 'ai'})
        ai._apply_control()
        self.assertEqual(desk.input_source, 'ai')
        ai.handle_bridge_message({'event': 'key', 'key': 'w', 'pressed': True})
        ai.handle_bridge_message({'event': 'mouse_move', 'x': 0.5, 'y': 0.25})
        sent = _bursts(root)
        self.assertIsInstance(sent[0], KeyEvent)
        self.assertEqual((sent[0].key, sent[0].pressed), ('w', True))
        self.assertIsInstance(sent[1], MouseEvent)
        self.assertEqual((sent[1].x, sent[1].y), (960, 270))

    def test_losing_the_ai_releases_its_keys_and_returns_control(self):
        ai, desk, root = _ai_with_desktop()
        ai.handle_bridge_message({'event': 'want_control', 'value': 'ai'})
        ai._apply_control()
        ai.handle_bridge_message({'event': 'key', 'key': 'w', 'pressed': True})
        ai.handle_bridge_message({'event': 'mouse_button', 'button': 'left', 'pressed': True})
        root.radio.burst.reset_mock()
        ai.bridge.ai_is_alive.return_value = False
        ai._apply_control()
        self.assertEqual(desk.input_source, 'human')
        sent = _bursts(root)
        self.assertTrue(any(isinstance(e, KeyEvent) and e.key == 'w' and not e.pressed for e in sent))
        self.assertTrue(any(isinstance(e, MouseEvent) and e.event_type == 2 for e in sent))
        self.assertEqual(desk._held_keys_brain, {})
        self.assertEqual(ai.wanted_source, 'human')

    def test_actions_are_counted_for_the_monitor(self):
        ai, desk, root = _ai_with_desktop()
        ai.handle_bridge_message({'event': 'want_control', 'value': 'ai'})
        ai._apply_control()
        ai.handle_bridge_message({'event': 'scroll', 'delta': 1})
        self.assertEqual(ai.action_counts, {'scroll': 1})
        self.assertEqual(ai.recent_actions[-1]['event'], 'scroll')


class TestDesktopKeepalive(unittest.TestCase):
    def test_refresh_loop_sends_an_empty_sparse_vector_for_the_endpoint_watchdog(self):
        import asyncio
        from robonet.buffers.buffer_objects import SparseVectorBuffer
        ai, desk, root = _ai_with_desktop()
        desk._running = True

        async def run_briefly():
            task = asyncio.ensure_future(desk._key_refresh_loop())
            await asyncio.sleep(0.05)
            desk._running = False
            await asyncio.sleep(0.3)
            task.cancel()

        asyncio.run(run_briefly())
        sent = [e for e in _bursts(root) if isinstance(e, SparseVectorBuffer)]
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0].val), 0)


class _Client(RobonetAIClient):
    def on_robonet_start(self): pass
    def on_robonet_shutdown(self): pass
    def on_robonet_connect(self, name): pass
    def on_robonet_disconnect(self): pass


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class TestLateChannel(unittest.TestCase):
    def test_client_attaches_to_a_channel_created_after_it_connected(self):
        address = ('localhost', 61100 + (hash(uuid.uuid4()) % 3000))
        server = BridgeServer(address=address)
        server.start()
        client = _Client(address=address)
        try:
            self.assertTrue(client.connect(timeout_s=5.0))
            self.assertTrue(_wait(lambda: server.connected))
            self.assertEqual(client.bridge.channels, {})
            server.write_channel('video_frame', 'video', b'x' * 100)
            server.send_sticky('frame_info:video', {'event': 'frame_info', 'label': 'video',
                                                    'shape': [10, 10], 'dtype': 'uint8'})

            def drained():
                msg = client.bridge.recv(0.05)
                if msg is not None:
                    client._dispatch(msg)
                return 'video' in client.bridge.channels and 'video' in client.channel_info
            self.assertTrue(_wait(drained))
            self.assertEqual(client.bridge.channels['video'].read(), b'x' * 100)
            self.assertEqual(client.channel_info['video'], {'shape': (10, 10), 'dtype': 'uint8'})
        finally:
            client.close()
            server.stop()


if __name__ == '__main__':
    unittest.main()


class TestRelativeMouse(unittest.TestCase):
    def test_message_round_trips_with_negative_offsets(self):
        from robonet.buffers.buffer_handling import pack_obj, unpack_obj
        from robonet.buffers.buffer_objects import MouseMoveRel
        obj = unpack_obj(pack_obj(MouseMoveRel(dx=-37, dy=120)))
        self.assertIsInstance(obj, MouseMoveRel)
        self.assertEqual((obj.dx, obj.dy), (-37, 120))

    def test_ai_relative_move_sends_pixel_offsets_not_positions(self):
        from robonet.buffers.buffer_objects import MouseMoveRel
        ai, desk, root = _ai_with_desktop()
        ai.handle_bridge_message({'event': 'want_control', 'value': 'ai'})
        ai._apply_control()
        for _ in range(3):  # three small moves add up: nothing re-anchors at the centre
            ai.handle_bridge_message({'event': 'mouse_move_rel', 'dx': 0.01, 'dy': -0.02})
        sent = [e for e in _bursts(root) if isinstance(e, MouseMoveRel)]
        self.assertEqual([(e.dx, e.dy) for e in sent], [(19, -22)] * 3)  # 0.01*1920=19.2, -0.02*1080=-21.6
        self.assertFalse(any(isinstance(e, MouseEvent) and e.event_type == 0 for e in _bursts(root)))

    def test_click_at_current_does_not_move_the_pointer(self):
        from robonet.buffers.buffer_objects import MOUSE_AT_CURRENT
        ai, desk, root = _ai_with_desktop()
        ai.handle_bridge_message({'event': 'want_control', 'value': 'ai'})
        ai._apply_control()
        ai.handle_bridge_message({'event': 'mouse_button', 'button': 'left', 'pressed': True, 'at_current': True})
        e = _bursts(root)[-1]
        self.assertEqual((e.event_type, e.x, e.y), (1, MOUSE_AT_CURRENT, MOUSE_AT_CURRENT))

    def test_endpoint_moves_relatively_and_clicks_in_place(self):
        from unittest.mock import patch
        from robonet.buffers.buffer_objects import MouseMoveRel, MouseEvent as ME, MOUSE_AT_CURRENT
        from robonet.endpoint import desktop_hardware as dh
        hw = dh.DesktopHw.__new__(dh.DesktopHw)
        hw._held_buttons = set()
        with patch.object(dh, 'pyautogui') as pg:
            hw._on_mouse_move_rel('brain', MouseMoveRel(dx=5, dy=-7))
            hw._on_mouse_event('brain', ME(event_type=1, x=MOUSE_AT_CURRENT, y=MOUSE_AT_CURRENT, button=1, delta=0))
            hw._on_mouse_event('brain', ME(event_type=2, x=10, y=20, button=1, delta=0))
        pg.moveRel.assert_called_once_with(5, -7)
        pg.mouseDown.assert_called_once_with(button='left')
        pg.mouseUp.assert_called_once_with(x=10, y=20, button='left')


class TestSpoofedCursor(unittest.TestCase):
    def test_pointer_state_round_trips(self):
        from robonet.buffers.buffer_handling import pack_obj, unpack_obj
        from robonet.buffers.buffer_objects import PointerState
        o = unpack_obj(pack_obj(PointerState(x=959, y=603, visible=1)))
        self.assertEqual((o.x, o.y, o.visible), (959, 603, 1))

    def test_forward_map_inverts_inverse_map_with_zoom_and_pan(self):
        from robonet.brain.util.viewport import Viewport
        v = Viewport()
        v.zoom_by(2.0, 1920, 1080, 640, 360)
        v.pan_by(0.1, -0.05, 1920, 1080, 640, 360)
        for fx, fy in ((0.5, 0.5), (0.31, 0.62), (0.7, 0.4)):
            cx, cy = v.forward_map(fx, fy, 1920, 1080, 640, 360)
            bx, by = v.inverse_map(cx / 640, cy / 360, 1920, 1080, 640, 360)
            self.assertAlmostEqual(bx, fx, places=3)
            self.assertAlmostEqual(by, fy, places=3)
        self.assertIsNone(v.forward_map(0.01, 0.01, 1920, 1080, 640, 360))  # outside the zoomed crop

    def _menu_with_pointer(self, visible, age=0.0):
        import time
        import numpy as np
        from robonet.brain.menu_system import MenuSubSystem
        m = MenuSubSystem.__new__(MenuSubSystem)
        m.out_res = (640, 360)
        m.last_img = np.zeros((1080, 1920, 3), np.uint8)
        ai, desk, root = _ai_with_desktop()
        desk._screen_width, desk._screen_height = 1920, 1080
        desk.pointer = (960, 540, visible, time.monotonic() - age)
        root.active_sub = desk
        m.root = root
        return m

    def test_cursor_drawn_at_the_pointer_only_when_shown_and_recent(self):
        import numpy as np
        frame = np.zeros((360, 640, 3), np.uint8)
        out = self._menu_with_pointer(True)._with_cursor(frame)
        self.assertTrue((frame == 0).all())  # the original (also the human's frame) is untouched
        tip_region = out[181:190, 320:325]
        self.assertGreater(int(tip_region.max()), 200)  # white arrow just below-right of (320, 180)
        self.assertTrue((self._menu_with_pointer(False)._with_cursor(frame) == 0).all())  # game hides it
        self.assertTrue((self._menu_with_pointer(True, age=5.0)._with_cursor(frame) == 0).all())  # stale
