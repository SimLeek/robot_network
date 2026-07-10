"""
tests/test_endpoint_handshake.py

RobotState (robonet/endpoint/radio_system.py): the previous brain
session, once it died anywhere past greeting, permanently stuck the
endpoint since a fresh WhoAreYou was only handled from listening/
greeting and logged as an error everywhere else. Covers the
restart_handshake transition that fixes this.
"""

import unittest
from unittest.mock import MagicMock, patch

from robonet.endpoint.radio_system import RobotState


class TestRobotStateRestartHandshake(unittest.TestCase):

    def test_greeting_acknowledged_restarts_to_greeting(self):
        sm = RobotState()
        sm.who_are_you_received()   # listening -> greeting
        sm.ack_received()           # greeting -> greeting_acknowledged

        sm.restart_handshake()

        self.assertTrue(sm.greeting.is_active)

    def test_explaining_restarts_to_greeting(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()  # -> explaining

        sm.restart_handshake()

        self.assertTrue(sm.greeting.is_active)

    def test_explaining_acknowledged_restarts_to_greeting(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.ack_received()
        sm.what_are_your_capabilities_received()
        sm.ack2_received()  # -> explaining_acknowledged

        sm.restart_handshake()

        self.assertTrue(sm.greeting.is_active)

    def test_streaming_restarts_to_greeting(self):
        sm = RobotState()
        sm.who_are_you_received()
        sm.chosen_received()  # greeting -> streaming

        sm.restart_handshake()

        self.assertTrue(sm.greeting.is_active)

    def test_listening_has_no_restart_transition(self):
        sm = RobotState()
        with self.assertRaises(Exception):
            sm.restart_handshake()


class TestOnWhoAreYou(unittest.TestCase):
    """RobotRadio._on_who_are_you against each stuck state -- confirms
    the actual handler (not just the raw state machine) recovers and
    always re-sends the WhoAreYou echo."""

    def _make_radio(self):
        with patch('robonet.endpoint.radio_system.open',
                  MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: b'x' * 32),
                                                    __exit__=lambda *a: None))), \
             patch('robonet.endpoint.radio_system.SecureRadioEngine'), \
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
            radio.endpoint_type = 'desktop'
            return radio

    def _make_who_are_you(self):
        obj = MagicMock()
        obj.ip = None  # already connected -- ip re-learning path not under test here
        return obj

    def test_from_listening_transitions_and_echoes(self):
        radio = self._make_radio()

        radio._on_who_are_you('brain', self._make_who_are_you())

        self.assertTrue(radio._sm.greeting.is_active)
        radio._engine.send_burst.assert_called_once()

    def test_from_greeting_stays_and_echoes(self):
        radio = self._make_radio()
        radio._sm.who_are_you_received()

        radio._on_who_are_you('brain', self._make_who_are_you())

        self.assertTrue(radio._sm.greeting.is_active)
        radio._engine.send_burst.assert_called_once()

    def test_from_explaining_restarts_and_echoes(self):
        radio = self._make_radio()
        radio._sm.who_are_you_received()
        radio._sm.ack_received()
        radio._sm.what_are_your_capabilities_received()

        radio._on_who_are_you('brain', self._make_who_are_you())

        self.assertTrue(radio._sm.greeting.is_active)
        radio._engine.send_burst.assert_called_once()

    def test_from_streaming_restarts_and_echoes(self):
        radio = self._make_radio()
        radio._sm.who_are_you_received()
        radio._sm.chosen_received()

        radio._on_who_are_you('brain', self._make_who_are_you())

        self.assertTrue(radio._sm.greeting.is_active)
        radio._engine.send_burst.assert_called_once()


class TestCapabilitiesReceivedFlag(unittest.TestCase):
    """axes/streams truthiness used to gate readiness -- desktop
    endpoints have permanently empty axes/streams by design, so that
    check could never pass. capabilities_received is a real flag set
    once, independent of what's in axes/streams."""

    def test_endpoint_defaults_to_not_received(self):
        from robonet.brain.util.network_scanner import Endpoint
        ep = Endpoint(ip='10.0.0.5')
        self.assertFalse(ep.capabilities_received)


if __name__ == '__main__':
    unittest.main()
