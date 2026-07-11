"""
tests/test_endpoint_handshake.py

RobotState (robonet/endpoint/radio_system.py) must never dead-end: every
handshake message, received in any state, either advances to a sensible
target or safely no-ops -- never "error, received while in X state" and
drops the message. Three layers:

  1. TestStateMachineMatrix -- every (state, event) pair the state
     machine should accept, and confirmation that the couple of pairs
     deliberately left undefined (an ack for something we haven't sent
     yet) really do raise, matching each handler's own guard for them.
  2. TestHandlers -- the actual handler methods, confirming the
     always-respond-to-cheap-messages / stay-idempotent-for-expensive-
     ones behavior.
  3. TestChaosScenarios -- realistic sequences: full handshake,
     retransmission bursts (matching an actual captured log), messages
     arriving out of order, and a brain process dying and restarting at
     every point in the handshake.

No time.sleep/delays anywhere -- direct, synchronous calls throughout.
"""

import unittest
from unittest.mock import MagicMock, patch

from statemachine.exceptions import TransitionNotAllowed

from robonet.endpoint.radio_system import RobotState


STATES = ['listening', 'greeting', 'greeting_acknowledged',
         'explaining', 'explaining_acknowledged', 'streaming']

# The linear path used to drive the state machine to an arbitrary state
# for test setup -- each step is itself asserted valid by the matrix
# test below, so using it to reach a starting point isn't circular.
_ADVANCE = {
    'listening': None,
    'greeting': 'who_are_you_received',
    'greeting_acknowledged': 'ack_received',
    'explaining': 'what_are_your_capabilities_received',
    'explaining_acknowledged': 'ack2_received',
    'streaming': 'chosen_received',
}

# event -> {source_state: expected_target_state, or None if the state
# machine should refuse the transition -- the handler guards against
# calling it in that state instead (see TestHandlers).
EXPECTED = {
    'who_are_you_received': {
        'listening': 'greeting', 'greeting': 'greeting',
        'greeting_acknowledged': 'greeting_acknowledged', 'explaining': 'explaining',
        'explaining_acknowledged': 'explaining_acknowledged', 'streaming': 'greeting',
    },
    'ack_received': {
        'listening': None, 'greeting': 'greeting_acknowledged',
        'greeting_acknowledged': 'greeting_acknowledged', 'explaining': 'explaining',
        'explaining_acknowledged': 'explaining_acknowledged', 'streaming': 'streaming',
    },
    'what_are_your_capabilities_received': {
        'listening': 'explaining', 'greeting': 'explaining',
        'greeting_acknowledged': 'explaining', 'explaining': 'explaining',
        'explaining_acknowledged': 'explaining_acknowledged', 'streaming': 'streaming',
    },
    'ack2_received': {
        'listening': None, 'greeting': None, 'greeting_acknowledged': None,
        'explaining': 'explaining_acknowledged',
        'explaining_acknowledged': 'explaining_acknowledged', 'streaming': 'streaming',
    },
    'chosen_received': {
        'listening': 'streaming', 'greeting': 'streaming',
        'greeting_acknowledged': 'streaming', 'explaining': 'streaming',
        'explaining_acknowledged': 'streaming', 'streaming': 'streaming',
    },
    'stop_received': {
        'listening': 'listening', 'greeting': 'listening',
        'greeting_acknowledged': 'listening', 'explaining': 'listening',
        'explaining_acknowledged': 'listening', 'streaming': 'listening',
    },
}


def _drive_to(sm: RobotState, state: str):
    """Advance sm along the normal path up to (and including) state."""
    for s in STATES:
        if _ADVANCE[s] is not None:
            getattr(sm, _ADVANCE[s])()
        if s == state:
            return


class TestStateMachineMatrix(unittest.TestCase):
    """Every (state, event) pair -- 6 states x 6 events = 36 cases."""

    def test_every_defined_transition_lands_at_its_expected_target(self):
        for event, per_state in EXPECTED.items():
            for source, target in per_state.items():
                if target is None:
                    continue
                with self.subTest(event=event, source=source, target=target):
                    sm = RobotState()
                    _drive_to(sm, source)
                    getattr(sm, event)()  # must not raise
                    self.assertEqual(sm.current_state_value, target)

    def test_every_undefined_transition_actually_raises(self):
        for event, per_state in EXPECTED.items():
            for source, target in per_state.items():
                if target is not None:
                    continue
                with self.subTest(event=event, source=source):
                    sm = RobotState()
                    _drive_to(sm, source)
                    with self.assertRaises(TransitionNotAllowed):
                        getattr(sm, event)()


class TestHandlers(unittest.TestCase):
    """The actual handler methods, not just the raw state machine."""

    def _make_radio(self, endpoint_type='desktop'):
        with patch('robonet.endpoint.radio_system.SecureRadioEngine'), \
             patch('robonet.endpoint.radio_system.zmq.asyncio.Context'):
            from robonet.endpoint.radio_system import RobotRadio
            radio = RobotRadio.__new__(RobotRadio)
            radio.root = MagicMock()
            radio._sm = RobotState()
            radio._radio_connected = True
            radio._server_ip = '10.0.0.5'
            radio._radio = MagicMock()
            radio._radio_lock = MagicMock()
            radio._engine = MagicMock()
            radio._uid = 0
            radio.endpoint_type = endpoint_type
            return radio

    def _who_are_you(self, ip=None):
        obj = MagicMock()
        obj.ip = ip
        return obj

    def _matching(self, radio):
        obj = MagicMock()
        obj.hostname = __import__('socket').gethostname()
        obj.endpoint_type = radio.endpoint_type
        return obj

    def _mismatched(self, radio):
        obj = MagicMock()
        obj.hostname = 'someone-elses-hostname'
        obj.endpoint_type = radio.endpoint_type
        return obj

    def test_who_are_you_always_echoes_regardless_of_state(self):
        for state in STATES:
            with self.subTest(state=state):
                radio = self._make_radio()
                _drive_to(radio._sm, state)
                radio._engine.reset_mock()

                radio._on_who_are_you('brain', self._who_are_you())

                radio._engine.send_burst.assert_called_once()

    def test_ack_from_listening_is_silently_ignored(self):
        radio = self._make_radio()
        radio._on_who_are_you_ack('brain', self._matching(radio))  # must not raise
        self.assertTrue(radio._sm.listening.is_active)  # unchanged

    def test_ack_mismatched_identity_ignored(self):
        radio = self._make_radio()
        radio._sm.who_are_you_received()
        radio._on_who_are_you_ack('brain', self._mismatched(radio))
        self.assertTrue(radio._sm.greeting.is_active)  # unchanged

    def test_ack_from_every_other_state_does_not_raise(self):
        for state in STATES:
            if state == 'listening':
                continue
            with self.subTest(state=state):
                radio = self._make_radio()
                _drive_to(radio._sm, state)
                radio._on_who_are_you_ack('brain', self._matching(radio))  # must not raise

    def test_capabilities_request_always_responds(self):
        from robonet.buffers.buffer_objects import RobotCapabilities
        for state in STATES:
            with self.subTest(state=state):
                radio = self._make_radio()
                _drive_to(radio._sm, state)
                radio.root.hardware.build_capabilities.return_value = RobotCapabilities.build(
                    axes=[], streams=[], hostname='x', endpoint_type='desktop')
                radio._engine.reset_mock()

                radio.on_what_are_your_capabilities('brain', MagicMock())

                radio._engine.send_burst.assert_called_once()

    def test_capabilities_ack_before_explaining_is_silently_ignored(self):
        for state in ['listening', 'greeting', 'greeting_acknowledged']:
            with self.subTest(state=state):
                radio = self._make_radio()
                _drive_to(radio._sm, state)

                radio.on_what_are_your_capabilities_ack('brain', self._matching(radio))  # must not raise

                self.assertEqual(radio._sm.current_state_value, state)  # unchanged

    def test_capabilities_ack_mismatched_identity_ignored(self):
        radio = self._make_radio()
        _drive_to(radio._sm, 'explaining')
        radio.on_what_are_your_capabilities_ack('brain', self._mismatched(radio))
        self.assertTrue(radio._sm.explaining.is_active)  # unchanged

    def test_robot_start_starts_pipeline_once(self):
        radio = self._make_radio()
        _drive_to(radio._sm, 'explaining_acknowledged')

        radio._on_robot_start('brain', self._matching(radio))

        self.assertTrue(radio._sm.streaming.is_active)
        radio.root.start.assert_called_once()

    def test_robot_start_duplicate_does_not_restart_pipeline(self):
        radio = self._make_radio()
        _drive_to(radio._sm, 'streaming')
        radio.root.start.reset_mock()

        radio._on_robot_start('brain', self._matching(radio))  # duplicate

        self.assertTrue(radio._sm.streaming.is_active)
        radio.root.start.assert_not_called()

    def test_robot_start_mismatched_identity_ignored(self):
        radio = self._make_radio()
        _drive_to(radio._sm, 'explaining_acknowledged')

        radio._on_robot_start('brain', self._mismatched(radio))

        self.assertTrue(radio._sm.explaining_acknowledged.is_active)  # unchanged
        radio.root.start.assert_not_called()


class TestChaosScenarios(unittest.TestCase):
    """Realistic message sequences a real network actually produces."""

    def test_normal_full_handshake(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()
        sm.chosen_received()
        self.assertTrue(sm.streaming.is_active)

    def test_retransmission_burst_at_every_step(self):
        # Matches an actual captured log: each step arrives 2-4 times in
        # a row before the next step's message shows up.
        sm = RobotState()
        for _ in range(3):
            sm.who_are_you_received()
        self.assertTrue(sm.greeting.is_active)
        for _ in range(4):
            sm.ack_received()
        self.assertTrue(sm.greeting_acknowledged.is_active)
        for _ in range(2):
            sm.what_are_your_capabilities_received()
        self.assertTrue(sm.explaining.is_active)
        for _ in range(3):
            sm.ack2_received()
        self.assertTrue(sm.explaining_acknowledged.is_active)
        for _ in range(2):
            sm.chosen_received()
        self.assertTrue(sm.streaming.is_active)

    def test_capabilities_request_arrives_before_its_ack(self):
        # The actual bug from the log: WhatAreYourCapabilities showed up
        # while still in 'greeting' (its own ack_received hadn't been
        # processed yet due to burst timing).
        sm = RobotState()
        sm.who_are_you_received()
        sm.what_are_your_capabilities_received()  # arrives early
        self.assertTrue(sm.explaining.is_active)
        sm.ack_received()  # the "late" ack shows up after -- must not raise or regress
        self.assertTrue(sm.explaining.is_active)

    def test_capabilities_ack_duplicate_does_not_raise(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()
        sm.ack2_received()  # duplicate -- the exact log case, must not raise
        self.assertTrue(sm.explaining_acknowledged.is_active)

    def test_stale_who_are_you_mid_handshake_does_not_regress(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        # A reordered, stale WhoAreYou from earlier in the burst arrives
        # late -- must not knock us back to greeting.
        sm.who_are_you_received()
        self.assertTrue(sm.explaining.is_active)

    def test_brain_restart_after_greeting_recovers(self):
        # A new brain session doesn't need who_are_you to force a reset
        # back to greeting -- self-loops still let its own subsequent
        # messages (its own ack, its own capabilities request) drive
        # the state machine forward normally. What matters is that the
        # full sequence still converges, not that any one message
        # resets us to a specific intermediate state.
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.who_are_you_received()  # brain died, a new one starts fresh
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()
        sm.chosen_received()
        self.assertTrue(sm.streaming.is_active)

    def test_brain_restart_after_explaining_recovers(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.who_are_you_received()  # brain died
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()
        sm.chosen_received()
        self.assertTrue(sm.streaming.is_active)

    def test_brain_restart_while_streaming_recovers(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()
        sm.chosen_received()
        self.assertTrue(sm.streaming.is_active)
        sm.who_are_you_received()  # brain died, a new one starts fresh
        self.assertTrue(sm.greeting.is_active)
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()
        sm.chosen_received()
        self.assertTrue(sm.streaming.is_active)

    def test_watchdog_stop_from_any_point_then_full_handshake_recovers(self):
        for state in STATES:
            with self.subTest(interrupted_at=state):
                sm = RobotState()
                _drive_to(sm, state)
                sm.stop_received()
                self.assertTrue(sm.listening.is_active)
                sm.who_are_you_received()
                sm.ack_received()
                sm.what_are_your_capabilities_received()
                sm.ack2_received()
                sm.chosen_received()
                self.assertTrue(sm.streaming.is_active)


if __name__ == '__main__':
    unittest.main()
