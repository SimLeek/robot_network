"""
robonet/bridge/ai_client_base.py

The abstract base class an AI process implements to react to robonet's
lifecycle over the bridge. Four callbacks are required because
different AI implementations need different things from them: a
simple/standard AI can just use on_robonet_connect/on_robonet_disconnect
("start controlling when connected, stop when not"), while a more
sophisticated, stable AI might use on_robonet_start/on_robonet_shutdown
to directly manage the (currently somewhat fragile) menu system itself,
or run its own restart-attempt logic on shutdown. Not deciding that
here -- just providing the seam.

Subclass this, implement the four required callbacks, optionally
override on_channel_data/on_tick for the actual video/audio/whatever
work, then call connect() and run(). run() is what replaces a
hand-written `while True: ...` polling loop: it owns the loop, drains
control events and dispatches them to the right callback, and reads
every shared memory channel once per iteration.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from robonet.bridge.bridge_control import BridgeClient, DEFAULT_ADDRESS, DEFAULT_AUTHKEY

log = logging.getLogger(__name__)


class RobonetAIClient(ABC):
    """See module docstring. self.bridge is the underlying
    BridgeClient -- self.bridge.channels is available directly if a
    subclass wants more control than on_channel_data gives it."""

    def __init__(self, address=DEFAULT_ADDRESS, authkey: bytes = DEFAULT_AUTHKEY,
                heartbeat_interval_s: float = 1.0):
        self.bridge = BridgeClient(address=address, authkey=authkey)
        self._running = False
        self._heartbeat_interval_s = heartbeat_interval_s
        self._last_heartbeat_sent: float = 0.0
        self.brain_health: Optional[dict] = None  # last {'channels': {...}} received via on_robonet_health

    # -- required: every AI implementation must decide what these mean to it --

    @abstractmethod
    def on_robonet_start(self) -> None:
        """Robonet is running -- fires once, right when this process's
        connection to it completes (regardless of whether robonet
        actually started long before or just before that)."""
        raise NotImplementedError

    @abstractmethod
    def on_robonet_shutdown(self) -> None:
        """Robonet is shutting down. run() stops its own loop right
        after this returns -- the connection is going away regardless
        of what this callback does."""
        raise NotImplementedError

    @abstractmethod
    def on_robonet_connect(self, endpoint_name: Optional[str]) -> None:
        """Robonet connected to a remote endpoint (not this AI<->
        robonet bridge connection -- see on_robonet_start for that)."""
        raise NotImplementedError

    @abstractmethod
    def on_robonet_disconnect(self) -> None:
        """Robonet disconnected from whatever remote endpoint it had."""
        raise NotImplementedError

    # -- optional: override whichever of these actually matters --

    def on_channel_data(self, label: str, data: bytes) -> None:
        """Fresh data was read from a channel this iteration (e.g.
        label='video' or 'audio', depending on whatever channels
        robonet actually declared during the handshake -- not
        hardcoded here). Default: no-op."""
        pass

    def on_robonet_health(self, health: dict) -> None:
        """Robonet's own framerate/staleness per channel -- is it
        actually running as expected, not just connected. The same
        info is kept on self.brain_health if a subclass would rather
        poll it than override this. Default: no-op."""
        pass

    def on_selection_text(self, text: str) -> None:
        """Text read from the endpoint's highlighted (X11 PRIMARY)
        selection, in response to whatever triggered the read (e.g.
        DesktopSubSystem.ai_read_selection() on the robonet side).
        Always a non-empty string -- 'no text highlighted' if nothing
        was selected. Default: no-op."""
        pass

    def on_tick(self) -> None:
        """Called once per run() iteration regardless of whether
        anything happened, for a subclass's own periodic work without
        needing to reimplement the loop. Default: no-op."""
        pass

    # -- connection + the loop itself --

    def connect(self, retry_interval_s: float = 0.5, timeout_s: Optional[float] = None) -> bool:
        return self.bridge.connect(retry_interval_s=retry_interval_s, timeout_s=timeout_s)

    def run(self, poll_interval_s: float = 1.0 / 60) -> None:
        """Drains control events (dispatching to the four required
        callbacks) and every shared memory channel (dispatching to
        on_channel_data) once per iteration, plus on_tick() every
        iteration regardless. Sends a heartbeat every
        heartbeat_interval_s so the brain side can tell this process
        is genuinely alive, not just still connected. Returns when
        stop() is called or an on_robonet_shutdown event arrives."""
        self._running = True
        while self._running:
            msg = self.bridge.recv(timeout_s=0.0)
            if msg is not None:
                self._dispatch(msg)
                if not self._running:
                    break

            for label, channel in self.bridge.channels.items():
                data = channel.read()
                if data is not None:
                    self.on_channel_data(label, data)

            now = time.time()
            if now - self._last_heartbeat_sent >= self._heartbeat_interval_s:
                self.bridge.send({'event': 'heartbeat'})
                self._last_heartbeat_sent = now

            self.on_tick()
            time.sleep(poll_interval_s)

    def set_want_control(self, value: str) -> bool:
        """Tells robonet whether this AI wants control or wants the
        human to have it. Doesn't force anything on its own -- purely
        a signal robonet can act on (or not)."""
        return self.bridge.send({'event': 'want_control', 'value': value})

    def _dispatch(self, msg) -> None:
        event = msg.get('event') if isinstance(msg, dict) else None
        if event == 'start':
            self.on_robonet_start()
        elif event == 'shutdown':
            self.on_robonet_shutdown()
            self._running = False
        elif event == 'connect':
            self.on_robonet_connect(msg.get('endpoint'))
        elif event == 'disconnect':
            self.on_robonet_disconnect()
        elif event == 'health':
            self.brain_health = msg.get('channels')
            self.on_robonet_health(msg.get('channels'))
        elif event == 'selection_text':
            self.on_selection_text(msg.get('text'))
        elif event is not None:
            log.warning(f'[ai-client] unrecognized event {event!r} -- ignoring')

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.bridge.close()
