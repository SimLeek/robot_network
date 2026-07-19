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
        server.stop()
        time.sleep(0.3)
        result = client.recv(timeout_s=1.0)  # must not raise
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
