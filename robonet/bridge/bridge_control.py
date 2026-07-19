"""
robonet/bridge/bridge_control.py

A control connection between the robonet/brain process and a separate
AI process, using multiprocessing.connection -- the same standard-
library family SharedMemory itself lives in, so nothing beyond the
standard library is needed anywhere in this bridge.

Handles exactly what shared memory isn't good at: the one-time
handshake describing which named channels exist and how big they are
(so the AI side can attach by name without hardcoding anything), plus
small, infrequent, arbitrary messages afterward -- status booleans,
lifecycle events. Both directions just send/recv picklable Python
objects; multiprocessing.connection handles framing and serialization.

BridgeServer (robonet/brain side) owns the shared memory channels and
listens; BridgeClient (the AI side) connects to it. Either side dying
must not take the other down: the server keeps listening for a new AI
connection if the current one drops, and reads/writes on either side
return None/False on a dead connection rather than raising.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Listener, Client, Connection
from typing import Any, Dict, Optional

from robonet.bridge.shmem_channel import ShmemChannel

log = logging.getLogger(__name__)

DEFAULT_ADDRESS = ('localhost', 60067)
DEFAULT_AUTHKEY = b'robonet-ai-bridge'


@dataclass
class ChannelSpec:
    """Describes one shared memory channel for the handshake -- name
    (the actual shared memory segment name) and capacity (must match
    on both sides) are load-bearing; label is just a human/AI-readable
    hint about what the channel carries (e.g. 'video', 'audio')."""
    name: str
    capacity_bytes: int
    label: str


class BridgeServer:
    """The side that owns the shared memory channels -- the robonet/
    brain process. Create channels, start(), then poll .connected or
    recv() for the AI process. Keeps listening for a fresh connection
    if the current one drops, so an AI crash never requires restarting
    the brain side.

    The background accept thread is the sole reader of the raw
    connection (forwarding real messages into a thread-safe queue) --
    it must never also call recv() itself outside of that, or it will
    silently steal messages meant for the public recv() method."""

    def __init__(self, address=DEFAULT_ADDRESS, authkey: bytes = DEFAULT_AUTHKEY):
        self._address = address
        self._authkey = authkey
        self._listener: Optional[Listener] = None
        self._conn: Optional[Connection] = None
        self._conn_lock = threading.Lock()
        self._accept_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._inbox: queue.Queue = queue.Queue()
        self.channels: Dict[str, ShmemChannel] = {}

    def create_channel(self, name: str, capacity_bytes: int, label: str) -> ShmemChannel:
        """Creates (or recreates) a named shared memory channel this
        server owns. Safe to call before or after start()."""
        if name in self.channels:
            self.channels[name].close()
            self.channels[name].unlink()
        ch = ShmemChannel(name, capacity_bytes, create=True, label=label)
        self.channels[name] = ch
        return ch

    @property
    def connected(self) -> bool:
        with self._conn_lock:
            return self._conn is not None

    def start(self) -> None:
        """Starts listening in the background -- does not block
        waiting for the AI process to actually connect, since it may
        take a while to spin up and the brain must keep running
        normally in the meantime."""
        self._listener = Listener(self._address, authkey=self._authkey)
        self._stop_event.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True,
                                               name='bridge-accept')
        self._accept_thread.start()
        log.info(f'[bridge] listening for the AI process on {self._address}')

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn = self._listener.accept()
            except OSError:
                break  # the listener itself was closed (stop() was called)
            with self._conn_lock:
                self._conn = conn
            try:
                conn.send({'channels': [ChannelSpec(ch.name, ch.capacity_bytes, ch.label)
                                        for ch in self.channels.values()]})
            except (OSError, EOFError):
                with self._conn_lock:
                    self._conn = None
                continue  # the AI process vanished before the handshake even landed -- fine, loop and wait for the next one
            log.info('[bridge] AI process connected')
            self._pump_until_disconnect(conn)
            with self._conn_lock:
                if self._conn is conn:
                    self._conn = None
            log.info('[bridge] AI process disconnected')

    def _pump_until_disconnect(self, conn: Connection) -> None:
        """This thread is the ONLY reader of the raw connection while
        it's live -- forwards every real message into a thread-safe
        queue for recv() to consume, and returns as soon as the
        connection dies so _accept_loop can go back to listening for
        the next AI process. send() is unaffected: it's called
        directly from whatever thread wants to send, which is safe as
        long as only one thing ever sends at a time."""
        while not self._stop_event.is_set():
            try:
                if not conn.poll(0.2):
                    continue
                msg = conn.recv()
            except (OSError, EOFError):
                return
            self._inbox.put(msg)

    def send(self, message: Any) -> bool:
        """Best-effort: returns False (never raises) if there's no
        connection right now, or if the send fails because the AI
        process just died -- an AI crash must not take the brain
        down."""
        with self._conn_lock:
            conn = self._conn
        if conn is None:
            return False
        try:
            conn.send(message)
            return True
        except (OSError, EOFError):
            return False

    def recv(self, timeout_s: float = 0.0) -> Optional[Any]:
        """Non-blocking by default. Returns None if there's nothing
        queued within timeout_s."""
        try:
            if timeout_s > 0:
                return self._inbox.get(timeout=timeout_s)
            return self._inbox.get_nowait()
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stop_event.set()
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
        for ch in self.channels.values():
            ch.close()
            ch.unlink()


class BridgeClient:
    """The AI-process side. Connects to a running BridgeServer, and
    attaches to whatever named channels the handshake describes --
    never hardcodes channel names/sizes, so the AI side doesn't need
    to know robonet's internals, just what channel labels mean."""

    def __init__(self, address=DEFAULT_ADDRESS, authkey: bytes = DEFAULT_AUTHKEY):
        self._address = address
        self._authkey = authkey
        self._conn: Optional[Connection] = None
        self.channels: Dict[str, ShmemChannel] = {}   # keyed by label
        self._specs: Dict[str, ChannelSpec] = {}      # keyed by label

    def connect(self, retry_interval_s: float = 0.5, timeout_s: Optional[float] = None) -> bool:
        """Retries until the robonet/brain process is listening (it
        may not have started yet, or may be mid-restart), or
        timeout_s elapses. Attaches to every channel from the
        handshake -- retries briefly if a channel's segment isn't
        visible to this process yet (a benign race right at startup)."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while deadline is None or time.monotonic() < deadline:
            try:
                self._conn = Client(self._address, authkey=self._authkey)
                break
            except (OSError, ConnectionRefusedError):
                time.sleep(retry_interval_s)
        else:
            return False

        try:
            handshake = self._conn.recv()
        except (OSError, EOFError):
            self._conn = None
            return False

        for spec in handshake.get('channels', []):
            self._specs[spec.label] = spec
            for attempt in range(20):
                try:
                    self.channels[spec.label] = ShmemChannel(
                        spec.name, spec.capacity_bytes, create=False)
                    break
                except FileNotFoundError:
                    time.sleep(0.1)
            else:
                log.error(f'[bridge] could not attach to channel {spec.name!r} '
                         f'({spec.label}) after repeated retries')
        return True

    def send(self, message: Any) -> bool:
        if self._conn is None:
            return False
        try:
            self._conn.send(message)
            return True
        except (OSError, EOFError):
            self._conn = None
            return False

    def recv(self, timeout_s: float = 0.0) -> Optional[Any]:
        if self._conn is None:
            return None
        try:
            if not self._conn.poll(timeout_s):
                return None
            return self._conn.recv()
        except (OSError, EOFError):
            self._conn = None
            return None

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        for ch in self.channels.values():
            ch.close()  # the AI side attaches, never owns -- never unlink()
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
