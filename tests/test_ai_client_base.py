"""
tests/test_ai_client_base.py

Tests RobonetAIClient: that it's genuinely abstract (all four
lifecycle callbacks required), that run() correctly dispatches every
event type to the right callback, that on_channel_data/on_tick fire as
documented, and -- with real separate processes -- that the full
lifecycle (start -> connect -> disconnect -> shutdown) actually fires
in the right order end to end, with run() returning cleanly on its own
once shutdown arrives.
"""

import multiprocessing as mp
import time
import unittest
import uuid

from robonet.bridge.ai_client_base import RobonetAIClient
from robonet.bridge.bridge_control import BridgeServer


def _unique_address():
    port = 60200 + (hash(uuid.uuid4()) % 5000)
    return ('localhost', port)


class _RecordingClient(RobonetAIClient):
    """A minimal concrete implementation for testing dispatch --
    records every callback invocation instead of doing real work."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = []

    def on_robonet_start(self):
        self.calls.append(('start',))

    def on_robonet_shutdown(self):
        self.calls.append(('shutdown',))

    def on_robonet_connect(self, endpoint_name):
        self.calls.append(('connect', endpoint_name))

    def on_robonet_disconnect(self):
        self.calls.append(('disconnect',))


class TestAbstractness(unittest.TestCase):

    def test_cannot_instantiate_without_implementing_all_four_callbacks(self):
        with self.assertRaises(TypeError):
            RobonetAIClient()

    def test_missing_even_one_callback_still_blocks_instantiation(self):
        class AlmostComplete(RobonetAIClient):
            def on_robonet_start(self): pass
            def on_robonet_shutdown(self): pass
            def on_robonet_connect(self, endpoint_name): pass
            # on_robonet_disconnect deliberately not implemented

        with self.assertRaises(TypeError):
            AlmostComplete()

    def test_implementing_all_four_allows_instantiation(self):
        client = _RecordingClient()  # must not raise
        self.assertIsInstance(client, RobonetAIClient)


class TestEventDispatch(unittest.TestCase):
    """Dispatch logic in isolation -- no real bridge connection
    needed, just feeding _dispatch() directly."""

    def test_start_event_calls_on_robonet_start(self):
        client = _RecordingClient()
        client._dispatch({'event': 'start'})
        self.assertEqual(client.calls, [('start',)])

    def test_connect_event_passes_the_endpoint_name(self):
        client = _RecordingClient()
        client._dispatch({'event': 'connect', 'endpoint': 'desk1'})
        self.assertEqual(client.calls, [('connect', 'desk1')])

    def test_disconnect_event_calls_on_robonet_disconnect(self):
        client = _RecordingClient()
        client._dispatch({'event': 'disconnect'})
        self.assertEqual(client.calls, [('disconnect',)])

    def test_shutdown_event_calls_on_robonet_shutdown_and_stops_running(self):
        client = _RecordingClient()
        client._running = True
        client._dispatch({'event': 'shutdown'})
        self.assertEqual(client.calls, [('shutdown',)])
        self.assertFalse(client._running)

    def test_unrecognized_event_is_ignored_not_raised(self):
        client = _RecordingClient()
        client._dispatch({'event': 'something_unexpected'})  # must not raise
        self.assertEqual(client.calls, [])

    def test_non_dict_message_is_ignored_not_raised(self):
        client = _RecordingClient()
        client._dispatch('a plain string message')  # must not raise
        self.assertEqual(client.calls, [])


class TestOptionalHooks(unittest.TestCase):

    def test_on_channel_data_default_is_a_harmless_noop(self):
        client = _RecordingClient()
        client.on_channel_data('video', b'data')  # must not raise

    def test_on_tick_default_is_a_harmless_noop(self):
        client = _RecordingClient()
        client.on_tick()  # must not raise

    def test_run_calls_on_channel_data_for_fresh_data_and_on_tick_every_iteration(self):
        from unittest.mock import MagicMock

        class HookedClient(_RecordingClient):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.channel_data_calls = []
                self.tick_count = 0
                self._iterations = 0

            def on_channel_data(self, label, data):
                self.channel_data_calls.append((label, data))

            def on_tick(self):
                self.tick_count += 1
                self._iterations += 1
                if self._iterations >= 3:
                    self.stop()

        client = HookedClient()
        fake_channel = MagicMock()
        fake_channel.read.side_effect = [b'frame1', None, b'frame2']
        client.bridge.channels = {'video': fake_channel}
        client.bridge.recv = MagicMock(return_value=None)

        client.run(poll_interval_s=0.0)

        self.assertEqual(client.channel_data_calls, [('video', b'frame1'), ('video', b'frame2')])
        self.assertEqual(client.tick_count, 3)


class TestFullLifecycleRealProcesses(unittest.TestCase):
    """The actual point of this ABC: with a real bridge and a real
    separate process, does the whole lifecycle fire in the right
    order, and does run() return cleanly on its own once shutdown
    arrives -- no hand-written polling loop needed at all."""

    def test_start_connect_disconnect_shutdown_fire_in_order(self):
        address = _unique_address()
        server = BridgeServer(address=address)
        server.create_channel('video_ch', capacity_bytes=64, label='video')
        server.start()

        results_queue = mp.Queue()

        def ai_proc():
            client = _RecordingClient(address=address)
            client.connect(timeout_s=10.0)
            client.run(poll_interval_s=0.01)
            results_queue.put(client.calls)
            client.close()

        p = mp.Process(target=ai_proc)
        p.start()

        t_end = time.time() + 10
        while not server.connected and time.time() < t_end:
            time.sleep(0.05)
        self.assertTrue(server.connected)

        server.notify_connect('desk1')
        time.sleep(0.2)
        server.notify_disconnect()
        time.sleep(0.2)
        server.stop()

        try:
            calls = results_queue.get(timeout=5)
        finally:
            p.join(timeout=5)

        self.assertEqual(calls, [
            ('start',),
            ('connect', 'desk1'),
            ('disconnect',),
            ('shutdown',),
        ])
        self.assertEqual(p.exitcode, 0)  # run() returned on its own, no hang, no crash


if __name__ == '__main__':
    unittest.main()


class TestHealthDispatch(unittest.TestCase):

    def test_health_event_calls_on_robonet_health_and_updates_brain_health(self):
        class HealthClient(_RecordingClient):
            def on_robonet_health(self, health):
                self.calls.append(('health', health))

        client = HealthClient()
        client._dispatch({'event': 'health', 'channels': {'video': {'framerate': 30.0}}})
        self.assertEqual(client.calls, [('health', {'video': {'framerate': 30.0}})])
        self.assertEqual(client.brain_health, {'video': {'framerate': 30.0}})

    def test_on_robonet_health_default_is_a_harmless_noop(self):
        client = _RecordingClient()
        client.on_robonet_health({'video': {}})  # must not raise


class TestSetWantControl(unittest.TestCase):

    def test_sends_the_want_control_event(self):
        from unittest.mock import MagicMock
        client = _RecordingClient()
        client.bridge.send = MagicMock(return_value=True)
        result = client.set_want_control('ai')
        client.bridge.send.assert_called_once_with({'event': 'want_control', 'value': 'ai'})
        self.assertTrue(result)


class TestAutomaticHeartbeat(unittest.TestCase):

    def test_run_sends_a_heartbeat_within_the_configured_interval(self):
        from unittest.mock import MagicMock

        class StopAfterTicks(_RecordingClient):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._ticks = 0

            def on_tick(self):
                self._ticks += 1
                if self._ticks >= 5:
                    self.stop()

        client = StopAfterTicks(heartbeat_interval_s=0.0)  # every iteration, for a fast/deterministic test
        client.bridge.recv = MagicMock(return_value=None)
        client.bridge.channels = {}
        client.bridge.send = MagicMock(return_value=True)

        client.run(poll_interval_s=0.0)

        sent_events = [c.args[0].get('event') for c in client.bridge.send.call_args_list]
        self.assertIn('heartbeat', sent_events)

    def test_heartbeat_not_sent_faster_than_the_configured_interval(self):
        from unittest.mock import MagicMock

        class StopAfterTicks(_RecordingClient):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._ticks = 0

            def on_tick(self):
                self._ticks += 1
                if self._ticks >= 20:
                    self.stop()

        client = StopAfterTicks(heartbeat_interval_s=999.0)  # effectively never again after the first
        client.bridge.recv = MagicMock(return_value=None)
        client.bridge.channels = {}
        client.bridge.send = MagicMock(return_value=True)

        client.run(poll_interval_s=0.0)

        sent_events = [c.args[0].get('event') for c in client.bridge.send.call_args_list]
        self.assertEqual(sent_events.count('heartbeat'), 1)  # only the first tick's heartbeat
