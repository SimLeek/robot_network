"""
tests/test_bridge_control.py

Tests BridgeServer/BridgeClient: the handshake, bidirectional
messaging, and -- the actual point of this whole thing -- the fault-
isolation property. Real multiprocessing throughout, not mocked; this
is pure standard library and runs fine in a sandbox.
"""

import multiprocessing as mp
import os
import time
import unittest
import uuid

from robonet.bridge.bridge_control import BridgeServer, BridgeClient


def _unique_address():
    # A distinct port per test so parallel/rapid test runs can't
    # collide on a still-closing socket from a previous test.
    port = 60100 + (hash(uuid.uuid4()) % 5000)
    return ('localhost', port)


def _make_server(address):
    server = BridgeServer(address=address)
    server.create_channel('video_ch', capacity_bytes=1024, label='video')
    server.start()
    return server


def _wait_for(predicate, timeout_s=10.0, interval_s=0.05):
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


class TestHandshake(unittest.TestCase):

    def test_client_attaches_to_the_channel_the_server_declared(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            ok = client.connect(timeout_s=10.0)
            self.assertTrue(ok)
            self.assertIn('video', client.channels)
            client.close()
        finally:
            server.stop()

    def test_client_channel_capacity_matches_the_server_s(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            self.assertEqual(client.channels['video'].capacity_bytes, 1024)
            client.close()
        finally:
            server.stop()

    def test_attached_channel_actually_works_for_real_data(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            server.channels['video_ch'].write(b'a real frame')
            data = None
            for _ in range(50):
                data = client.channels['video'].read()
                if data is not None:
                    break
                time.sleep(0.05)
            self.assertEqual(data, b'a real frame')
            client.close()
        finally:
            server.stop()

    def test_server_connected_becomes_true_after_a_client_attaches(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            self.assertFalse(server.connected)
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            self.assertTrue(_wait_for(lambda: server.connected))
            client.close()
        finally:
            server.stop()

    def test_connect_with_no_server_listening_times_out_and_returns_false(self):
        client = BridgeClient(address=_unique_address())
        ok = client.connect(timeout_s=0.5, retry_interval_s=0.1)
        self.assertFalse(ok)


class TestBidirectionalMessaging(unittest.TestCase):

    def test_client_to_server_message_arrives(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.send({'ai_running': True, 'want_control': 'human'})

            msg = None
            t_end = time.time() + 5
            while msg is None and time.time() < t_end:
                msg = server.recv(timeout_s=0.1)
            self.assertEqual(msg, {'ai_running': True, 'want_control': 'human'})
            client.close()
        finally:
            server.stop()

    def test_server_to_client_message_arrives(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # drain the automatic start event first
            self.assertTrue(_wait_for(lambda: server.connected))

            server.send({'brain_healthy': True, 'connected_endpoint': 'desk1'})
            msg = client.recv(timeout_s=5.0)
            self.assertEqual(msg, {'brain_healthy': True, 'connected_endpoint': 'desk1'})
            client.close()
        finally:
            server.stop()

    def test_multiple_messages_arrive_in_order(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.send('first')
            client.send('second')
            client.send('third')

            received = []
            t_end = time.time() + 5
            while len(received) < 3 and time.time() < t_end:
                msg = server.recv(timeout_s=0.1)
                if msg is not None:
                    received.append(msg)
            self.assertEqual(received, ['first', 'second', 'third'])
            client.close()
        finally:
            server.stop()

    def test_send_with_no_connection_returns_false_not_raise(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            self.assertFalse(server.send({'x': 1}))  # no client connected at all
        finally:
            server.stop()


def _ai_that_dies_immediately(address):
    client = BridgeClient(address=address)
    client.connect(timeout_s=10.0)
    os._exit(1)  # abrupt, unclean death -- no Python-level cleanup runs at all


def _ai_that_stays_alive(address, alive_s=2.0):
    client = BridgeClient(address=address)
    ok = client.connect(timeout_s=10.0)
    if ok:
        client.send({'ai_running': True})
    time.sleep(alive_s)
    client.close()


class TestFaultIsolation(unittest.TestCase):
    """The actual point of this bridge: an AI crashing must not take
    the brain down, and the brain must be able to accept a fresh AI
    connection afterward without needing to be restarted."""

    def test_server_survives_an_abrupt_ai_crash(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            p = mp.Process(target=_ai_that_dies_immediately, args=(address,))
            p.start()
            p.join(timeout=5)
            self.assertEqual(p.exitcode, 1)
            # The real assertion: the server process (this test) is
            # still running at all, and its internal state recovers.
            self.assertTrue(_wait_for(lambda: not server.connected))
        finally:
            server.stop()

    def test_accepts_a_fresh_connection_after_a_crash_without_restarting(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            dead = mp.Process(target=_ai_that_dies_immediately, args=(address,))
            dead.start()
            dead.join(timeout=5)
            self.assertTrue(_wait_for(lambda: not server.connected))

            alive = mp.Process(target=_ai_that_stays_alive, args=(address, 2.0))
            alive.start()

            self.assertTrue(_wait_for(lambda: server.connected))
            msg = None
            t_end = time.time() + 5
            while msg is None and time.time() < t_end:
                msg = server.recv(timeout_s=0.1)
            self.assertEqual(msg, {'ai_running': True})

            alive.join(timeout=5)
        finally:
            server.stop()

    def test_client_recv_does_not_raise_if_the_server_stops(self):
        address = _unique_address()
        server = _make_server(address)
        client = BridgeClient(address=address)
        client.connect(timeout_s=10.0)
        client.recv(timeout_s=5.0)  # drain the automatic start event first
        server.stop()
        client.recv(timeout_s=5.0)  # drain the automatic shutdown event too
        time.sleep(0.3)
        result = client.recv(timeout_s=1.0)  # now truly nothing left -- must not raise
        self.assertIsNone(result)
        client.close()

    def test_client_send_eventually_returns_false_if_the_server_stops(self):
        # A socket's first send() after the peer disconnects often
        # succeeds anyway -- the OS buffers it locally before a
        # broken-pipe error has a chance to surface. Standard TCP/
        # socket behavior, not specific to this code -- so check that
        # it's detected within a few attempts, not on the very first one.
        address = _unique_address()
        server = _make_server(address)
        client = BridgeClient(address=address)
        client.connect(timeout_s=10.0)
        server.stop()
        time.sleep(0.3)
        results = [client.send({'x': i}) for i in range(5)]  # must not raise
        self.assertFalse(results[-1])
        client.close()


if __name__ == '__main__':
    unittest.main()


class TestLifecycleEvents(unittest.TestCase):
    """notify_connect/notify_disconnect, and that a fresh connection
    automatically receives a start event right after the handshake."""

    def test_new_connection_receives_a_start_event(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            msg = client.recv(timeout_s=5.0)
            self.assertEqual(msg, {'event': 'start'})
            client.close()
        finally:
            server.stop()

    def test_notify_connect_reaches_the_client(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # drain the start event first
            self.assertTrue(_wait_for(lambda: server.connected))

            server.notify_connect('desk1')
            msg = client.recv(timeout_s=5.0)
            self.assertEqual(msg, {'event': 'connect', 'endpoint': 'desk1'})
            client.close()
        finally:
            server.stop()

    def test_notify_disconnect_reaches_the_client(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # drain the start event first
            self.assertTrue(_wait_for(lambda: server.connected))

            server.notify_disconnect()
            msg = client.recv(timeout_s=5.0)
            self.assertEqual(msg, {'event': 'disconnect'})
            client.close()
        finally:
            server.stop()

    def test_stop_sends_a_shutdown_event_before_tearing_down(self):
        address = _unique_address()
        server = _make_server(address)
        client = BridgeClient(address=address)
        client.connect(timeout_s=10.0)
        client.recv(timeout_s=5.0)  # drain the start event first
        self.assertTrue(_wait_for(lambda: server.connected))

        server.stop()
        msg = client.recv(timeout_s=5.0)
        self.assertEqual(msg, {'event': 'shutdown'})
        client.close()

    def test_notify_connect_with_no_client_returns_false_not_raise(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            self.assertFalse(server.notify_connect('desk1'))
        finally:
            server.stop()


class TestConnectedEndpointReplay(unittest.TestCase):
    """A late-joining AI must learn about an already-connected
    endpoint immediately, not just react to future transitions."""

    def test_late_joining_ai_learns_the_current_endpoint(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            server.notify_connect('desk1')  # no AI connected yet -- returns False, that's fine

            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            start_msg = client.recv(timeout_s=5.0)
            self.assertEqual(start_msg, {'event': 'start'})
            connect_msg = client.recv(timeout_s=5.0)
            self.assertEqual(connect_msg, {'event': 'connect', 'endpoint': 'desk1'})
            client.close()
        finally:
            server.stop()

    def test_no_replay_when_nothing_is_connected(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            start_msg = client.recv(timeout_s=5.0)
            self.assertEqual(start_msg, {'event': 'start'})
            # Nothing else queued -- no phantom connect event.
            self.assertIsNone(client.recv(timeout_s=0.3))
            client.close()
        finally:
            server.stop()

    def test_disconnect_clears_the_state_for_future_joiners(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            server.notify_connect('desk1')
            server.notify_disconnect()

            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # start
            self.assertIsNone(client.recv(timeout_s=0.3))  # no stale replay
            client.close()
        finally:
            server.stop()


class TestAIStatusTracking(unittest.TestCase):
    """Heartbeat and want_control -- observed in passing, not
    consumed, so recv() still sees them too."""

    def test_ai_is_alive_false_before_any_heartbeat(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            self.assertFalse(server.ai_is_alive())
        finally:
            server.stop()

    def test_ai_is_alive_true_after_a_heartbeat(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.send({'event': 'heartbeat'})
            self.assertTrue(_wait_for(lambda: server.ai_is_alive()))
            client.close()
        finally:
            server.stop()

    def test_heartbeat_still_arrives_via_recv_too(self):
        # Observed in passing must not mean consumed.
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.send({'event': 'heartbeat'})
            msg = None
            t_end = time.time() + 5
            while msg is None and time.time() < t_end:
                msg = server.recv(timeout_s=0.1)
            self.assertEqual(msg, {'event': 'heartbeat'})
            client.close()
        finally:
            server.stop()

    def test_ai_wants_control_tracks_the_latest_value(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.send({'event': 'want_control', 'value': 'ai'})
            self.assertTrue(_wait_for(lambda: server.ai_wants_control == 'ai'))
            client.send({'event': 'want_control', 'value': 'human'})
            self.assertTrue(_wait_for(lambda: server.ai_wants_control == 'human'))
            client.close()
        finally:
            server.stop()


class TestHealthReporting(unittest.TestCase):

    def test_compute_and_send_health_reaches_the_client(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # drain start
            self.assertTrue(_wait_for(lambda: server.connected))

            server.channels['video_ch'].write(b'frame')
            server.compute_and_send_health()

            msg = client.recv(timeout_s=5.0)
            self.assertEqual(msg['event'], 'health')
            self.assertIn('video', msg['channels'])
            self.assertIsNotNone(msg['channels']['video']['seconds_since_write'])
            client.close()
        finally:
            server.stop()

    def test_health_shows_none_staleness_for_a_never_written_channel(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # drain start
            self.assertTrue(_wait_for(lambda: server.connected))

            server.compute_and_send_health()  # video_ch never written to

            msg = client.recv(timeout_s=5.0)
            self.assertIsNone(msg['channels']['video']['seconds_since_write'])
            client.close()
        finally:
            server.stop()

    def test_framerate_reflects_writes_between_two_health_calls(self):
        address = _unique_address()
        server = _make_server(address)
        try:
            client = BridgeClient(address=address)
            client.connect(timeout_s=10.0)
            client.recv(timeout_s=5.0)  # drain start
            self.assertTrue(_wait_for(lambda: server.connected))

            server.compute_and_send_health()  # establishes the baseline
            client.recv(timeout_s=5.0)

            for _ in range(10):
                server.channels['video_ch'].write(b'frame')
            time.sleep(0.2)
            server.compute_and_send_health()

            msg = client.recv(timeout_s=5.0)
            self.assertGreater(msg['channels']['video']['framerate'], 0)
            client.close()
        finally:
            server.stop()


class TestWriteChannel(unittest.TestCase):
    """Lazily creates or resizes a channel automatically -- for data
    whose byte size isn't known until the first real payload arrives
    (e.g. video frame size, which depends on actual resolution)."""

    def test_creates_the_channel_on_first_write(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'x' * 100)
            self.assertIn('video_frame', server.channels)
        finally:
            server.stop()

    def test_written_data_reads_back_correctly(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'hello frame')
            self.assertEqual(server.channels['video_frame'].read(), b'hello frame')
        finally:
            server.stop()

    def test_label_is_set_correctly(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'data')
            self.assertEqual(server.channels['video_frame'].label, 'video')
        finally:
            server.stop()

    def test_same_size_write_does_not_recreate_the_channel(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'x' * 100)
            first = server.channels['video_frame']
            server.write_channel('video_frame', 'video', b'y' * 100)
            self.assertIs(server.channels['video_frame'], first)
        finally:
            server.stop()

    def test_smaller_write_does_not_recreate_the_channel(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'x' * 1000)
            first = server.channels['video_frame']
            server.write_channel('video_frame', 'video', b'y' * 10)
            self.assertIs(server.channels['video_frame'], first)
        finally:
            server.stop()

    def test_larger_write_recreates_with_more_capacity(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'x' * 100)
            first_capacity = server.channels['video_frame'].capacity_bytes
            server.write_channel('video_frame', 'video', b'y' * 100000)
            self.assertGreater(server.channels['video_frame'].capacity_bytes, first_capacity)
            self.assertEqual(server.channels['video_frame'].read(), b'y' * 100000)
        finally:
            server.stop()

    def test_two_different_channel_names_stay_independent(self):
        server = BridgeServer()
        try:
            server.write_channel('video_frame', 'video', b'video data')
            server.write_channel('audio_chunk', 'audio', b'audio data')
            self.assertEqual(server.channels['video_frame'].read(), b'video data')
            self.assertEqual(server.channels['audio_chunk'].read(), b'audio data')
        finally:
            server.stop()


class TestStopIsIdempotent(unittest.TestCase):
    """unlink() isn't safe to call twice (the OS resource is already
    gone after the first call) -- stop() must guard against being
    called more than once, since SubSystem.__del__ can call it again
    at garbage-collection time after an explicit stop()."""

    def test_calling_stop_twice_does_not_raise(self):
        server = BridgeServer()
        server.create_channel('video_ch', capacity_bytes=64, label='video')
        server.stop()
        server.stop()  # must not raise

    def test_calling_stop_three_times_does_not_raise(self):
        server = BridgeServer()
        server.write_channel('video_frame', 'video', b'data')
        server.stop()
        server.stop()
        server.stop()  # must not raise
