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
