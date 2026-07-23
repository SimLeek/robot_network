"""
tests/test_radio_capabilities_handler.py

Regression test for a real, reported bug: connecting to a desktop
endpoint needed two brain-side runs before the menu ever showed it as
ready, despite the endpoint correctly responding to repeated
WhatAreYourCapabilities requests.

Root cause: _robot_capabilities_handler's inline fallback lookup
required v.endpoint_type == obj.endpoint_type to find the scanner's
record for an endpoint -- but on the very first RobotCapabilities
message for a given endpoint, the scanner's own record still has
endpoint_type='unknown' (it doesn't learn the real type until this
very message tells it). The match always failed on that first receipt,
hitting the "Could not find endpoint" error-and-return path and never
setting capabilities_received, no matter how many times the endpoint
retried. enrich_endpoint already has the correct fix for this (it
matches by hostname alone) -- the handler was just duplicating the
lookup with an extra, incorrect condition instead of using it.
"""

import unittest
from unittest.mock import MagicMock, patch

from robonet.brain.radio_system import RadioSubSystem
from robonet.brain.util.network_scanner import Endpoint


def _make_radio(scanner_endpoint_type='unknown'):
    radio = RadioSubSystem.__new__(RadioSubSystem)
    radio._endpoints = {}
    radio.root = MagicMock()
    radio.burst = MagicMock()
    radio._maybe_auto_connect = MagicMock()
    radio.stop = MagicMock()  # avoid __del__ noise hunting for __init__-only attributes

    scanner_ep = Endpoint(ip='192.168.0.31', hostname='SimLeekArchAI0', endpoint_type=scanner_endpoint_type)
    radio._scanner = MagicMock()
    radio._scanner.by_ip = {'192.168.0.31': scanner_ep}
    radio._scanner.by_hostname = {}
    return radio


def _make_capabilities_obj(hostname='SimLeekArchAI0', endpoint_type='desktop'):
    obj = MagicMock()
    obj.hostname = hostname
    obj.endpoint_type = endpoint_type
    obj.axes.return_value = [{'name': 'keys_press'}]
    obj.streams.return_value = [{'name': 'screen', 'type': 'video'}]
    return obj


class TestRobotCapabilitiesHandlerFirstReceipt(unittest.TestCase):
    """The exact scenario that was broken: first-ever capabilities
    message for an endpoint the scanner only knows as 'unknown' so far."""

    def test_first_receipt_with_unknown_scanner_type_still_succeeds(self):
        radio = _make_radio(scanner_endpoint_type='unknown')
        handler = radio._robot_capabilities_handler(radio.root)
        obj = _make_capabilities_obj()

        handler('192.168.0.31', obj)

        ep = radio._endpoints.get('SimLeekArchAI0:desktop')
        self.assertIsNotNone(ep)
        self.assertTrue(ep.capabilities_received)

    def test_first_receipt_sends_the_ack(self):
        radio = _make_radio(scanner_endpoint_type='unknown')
        handler = radio._robot_capabilities_handler(radio.root)
        obj = _make_capabilities_obj()

        handler('192.168.0.31', obj)

        radio.burst.assert_called_once()

    def test_first_receipt_triggers_auto_connect_check(self):
        radio = _make_radio(scanner_endpoint_type='unknown')
        handler = radio._robot_capabilities_handler(radio.root)
        obj = _make_capabilities_obj()

        handler('192.168.0.31', obj)

        radio._maybe_auto_connect.assert_called_once()

    def test_second_receipt_for_same_endpoint_also_succeeds(self):
        # The retry/burst pattern means several of these can arrive in
        # a row -- confirm repeats stay fine, not just the first one.
        radio = _make_radio(scanner_endpoint_type='unknown')
        handler = radio._robot_capabilities_handler(radio.root)
        obj = _make_capabilities_obj()

        handler('192.168.0.31', obj)
        handler('192.168.0.31', obj)

        ep = radio._endpoints.get('SimLeekArchAI0:desktop')
        self.assertTrue(ep.capabilities_received)

    def test_receipt_when_scanner_already_knows_the_type_also_succeeds(self):
        # Not the buggy case, but confirm it still works: shouldn't
        # regress the scenario where the type happens to already match.
        radio = _make_radio(scanner_endpoint_type='desktop')
        handler = radio._robot_capabilities_handler(radio.root)
        obj = _make_capabilities_obj()

        handler('192.168.0.31', obj)

        ep = radio._endpoints.get('SimLeekArchAI0:desktop')
        self.assertIsNotNone(ep)
        self.assertTrue(ep.capabilities_received)

    def test_unknown_hostname_is_discarded_without_raising(self):
        radio = _make_radio(scanner_endpoint_type='unknown')
        handler = radio._robot_capabilities_handler(radio.root)
        obj = _make_capabilities_obj(hostname='never-seen-this-host')

        handler('192.168.0.99', obj)  # must not raise

        self.assertEqual(radio._endpoints, {})
        radio.burst.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class TestSelectionTextHandler(unittest.TestCase):
    """The brain side of the text-selection feature: always log for a
    human to see, and route through sm.ai (which handles its own
    optional bridge internally) -- the same pattern MenuSubSystem
    already uses to fan video/audio out to both displayer and ai,
    rather than reaching past AISubSystem into a separate sibling."""

    def _make_radio_for_selection(self):
        radio = RadioSubSystem.__new__(RadioSubSystem)
        return radio

    def test_forwards_to_ai_when_one_is_attached(self):
        radio = self._make_radio_for_selection()
        sm = MagicMock()
        from robonet.buffers.buffer_objects import SelectionText

        handler = radio._selection_text_handler(sm)
        handler('endpoint-host', SelectionText(text='hello from xsel'))

        sm.ai.update_selection_text.assert_called_once_with('hello from xsel')

    def test_does_not_raise_when_no_ai_is_attached(self):
        radio = self._make_radio_for_selection()
        sm = MagicMock()
        sm.ai = None
        from robonet.buffers.buffer_objects import SelectionText

        handler = radio._selection_text_handler(sm)
        handler('endpoint-host', SelectionText(text='hello'))  # must not raise

    def test_logs_for_a_human_regardless_of_whether_ai_is_attached(self):
        radio = self._make_radio_for_selection()
        sm = MagicMock()
        sm.ai = None
        from robonet.buffers.buffer_objects import SelectionText

        with patch('robonet.brain.radio_system.log') as mock_log:
            handler = radio._selection_text_handler(sm)
            handler('endpoint-host', SelectionText(text='some text'))

        logged = ' '.join(str(c.args[0]) for c in mock_log.info.call_args_list)
        self.assertIn('some text', logged)
