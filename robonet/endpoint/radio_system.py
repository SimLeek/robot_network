from __future__ import annotations

import asyncio
import random
import socket
import threading
import time
import typing

import zmq
from statemachine import StateMachine, State

from robonet.buffers.buffer_handling import pack_obj, unpack_obj
from robonet.buffers.buffer_objects import WhoAreYou, WhoAreYouAck, RobotCapabilities, RobotCapabilitiesAck, RobotStart
from robonet.receive_callbacks import receive_objs_encrypted
from robonet.util import SecureRadioEngine

if typing.TYPE_CHECKING:
    from robonet.endpoint.base import RobotNode

from robonet.logging_setup import setup_logging
import robonet.endpoint.settings as settings_

log = setup_logging()
settings = settings_.get()

WATCHDOG_INTERVAL = 1.0
WATCHDOG_TIMEOUT = 10.0
HOSTNAME = socket.gethostname()


class RobotState(StateMachine):
    """
    listening -> greeting -> greeting_acknowledged -> explaining ->
    explaining_acknowledged -> streaming -> (stop) -> listening

    Every event below is defined for every state it could plausibly
    arrive in, not just the "expected" one -- retransmitted/duplicate/
    reordered messages are a normal occurrence (the brain side bursts
    each step a few times for reliability), and a message arriving in
    an unexpected state must never be a dead end. Two rules used
    throughout: (1) if we're already at or past a message's target,
    receiving it again is a same-state self-loop, not an error; (2) if
    we're earlier than its target, it jumps us forward. who_are_you is
    the one exception that can move backward (streaming -> greeting):
    a fresh WhoAreYou after we're already streaming means a new brain
    session has started and the old one is gone, not a duplicate of the
    one we already answered.
    """
    listening = State(initial=True)
    greeting = State()
    greeting_acknowledged = State()
    explaining = State()
    explaining_acknowledged = State()
    streaming = State()

    who_are_you_received = (
        listening.to(greeting)
        | greeting.to(greeting)
        | greeting_acknowledged.to(greeting_acknowledged)
        | explaining.to(explaining)
        | explaining_acknowledged.to(explaining_acknowledged)
        | streaming.to(greeting)
    )

    # Not defined from `listening` -- an ack before we've even echoed
    # WhoAreYou has no sensible target, so the handler skips calling
    # this at all rather than the state machine having to reject it.
    ack_received = (
        greeting.to(greeting_acknowledged)
        | greeting_acknowledged.to(greeting_acknowledged)
        | explaining.to(explaining)
        | explaining_acknowledged.to(explaining_acknowledged)
        | streaming.to(streaming)
    )

    # Defined from every state, including listening -- if the brain
    # asks for capabilities, it already believes greeting succeeded, so
    # treat receiving this at all as sufficient to skip straight there.
    what_are_your_capabilities_received = (
        listening.to(explaining)
        | greeting.to(explaining)
        | greeting_acknowledged.to(explaining)
        | explaining.to(explaining)
        | explaining_acknowledged.to(explaining_acknowledged)
        | streaming.to(streaming)
    )

    # Not defined from listening/greeting/greeting_acknowledged -- an
    # ack for capabilities we haven't sent yet has no sensible target.
    ack2_received = (
        explaining.to(explaining_acknowledged)
        | explaining_acknowledged.to(explaining_acknowledged)
        | streaming.to(streaming)
    )

    chosen_received = (
        listening.to(streaming)
        | greeting.to(streaming)
        | greeting_acknowledged.to(streaming)
        | explaining.to(streaming)
        | explaining_acknowledged.to(streaming)
        | streaming.to(streaming)
    )

    stop_received = (
        listening.to(listening)
        | greeting.to(listening)
        | greeting_acknowledged.to(listening)
        | explaining.to(listening)
        | explaining_acknowledged.to(listening)
        | streaming.to(listening)
    )

    def on_transition(self, event, source, target):
        if source is not target:
            log.info("handshake: %s -> %s  [%s]", source.id, target.id, event)
        else:
            log.debug("handshake: %s  [%s, ignored]", source.id, event)


class RobotRadio:
    def __init__(self, endpoint_type='robot'):
        self.root = None
        self._sm = RobotState()
        self._last_ctrl = time.monotonic()
        self.endpoint_type = endpoint_type

        with open(settings["psk_file"], "rb") as f:
            psk = f.read()
        with open(settings["server_psk_file"], "rb") as f:
            server_psk = f.read()

        self._psk = psk
        self._server_psk = server_psk
        self._engine = SecureRadioEngine(psk)
        self._radio_lock = threading.Lock()

        ctx = zmq.asyncio.Context.instance()
        self._dish = ctx.socket(zmq.DISH)
        self._radio = ctx.socket(zmq.RADIO)
        for sock in (self._dish, self._radio):
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.CONFLATE, 1)
        self._radio.setsockopt(zmq.SNDHWM, 15)
        self._dish.setsockopt(zmq.RCVHWM, 15)
        self._dish.rcvtimeo = 1

        our_port = settings["our_port"]
        their_port = settings["their_port"]

        self._dish.bind(f"udp://0.0.0.0:{our_port}")
        self._dish.join("direct")
        log.info("listening on :%d (server will discover us)", our_port)
        self._uid=0
        self._radio_connected = False
        self._server_ip = None

        # Static IP on the shared wired subnet, for NetMode.WIRED.
        if settings["auto_wired_setup"]:
            try:
                from robonet.wired.util import connect_wired
                wired_iface = connect_wired()
                log.info("wired static IP configured on %s", wired_iface)
            except Exception as e:
                log.info("wired auto-setup skipped: %s -- try examples/setup_eth_client.py", e)

    def setup(self, parent: 'RobotNode'):
        self.root = parent
        log.info("'%s' online", HOSTNAME)

    def stop(self):
        self._radio.close()
        self._dish.close()

    def burst(self, obj):
        # too noisy even for info
        #log.info(f"burst: {type(obj)}")
        data = pack_obj(obj)
        parts = [data[i: i + 4096] for i in range(0, len(data), 4096)]
        self._uid = (self._uid + 1) % 256
        self._engine.send_burst(
            self._radio_lock, self._radio, self._uid, parts
        )

    def is_streaming(self) -> bool:
        return self._sm.streaming.is_active

    @property
    def handlers(self) -> dict:
        return {
            "WhoAreYou": self._on_who_are_you,
            "WhoAreYouAck": self._on_who_are_you_ack,
            "RobotCapabilities": self.on_what_are_your_capabilities,
            "RobotCapabilitiesAck": self.on_what_are_your_capabilities_ack,
            "RobotStart": self._on_robot_start,
        }

    def _on_who_are_you(self, hostname: str, obj: WhoAreYou):
        log.info("Received who are you")

        if not self._radio_connected and obj.ip:
            self._server_ip = obj.ip
            self.root.update_server_ip(obj.ip)
            self._radio.connect(f'udp://{obj.ip}:{settings["their_port"]}')
            self._radio_connected = True
            log.info(f"[radio] learned IP {obj.ip} from WhoAreYou")
        if self._radio_connected:  # no point in sending if we can't talk
            self._sm.who_are_you_received()
            self.burst(WhoAreYou(hostname=HOSTNAME, endpoint_type=self.endpoint_type))

    def _on_who_are_you_ack(self, hostname: str, obj: WhoAreYouAck):
        log.info("received who are you ack")
        if not (obj.hostname == HOSTNAME and obj.endpoint_type == self.endpoint_type):
            return
        log.info("ack was for us")
        if self._sm.listening.is_active:
            return  # too early -- haven't echoed WhoAreYou yet; a retry will arrive once we have
        self._sm.ack_received()

    def on_what_are_your_capabilities(self, hostname: str, obj: RobotCapabilities):
        log.info("Received what are your capabilities")
        self._sm.what_are_your_capabilities_received()
        response = self.root.hardware.build_capabilities()
        response.hostname = HOSTNAME
        response.endpoint_type = self.endpoint_type
        self.burst(response)

    def on_what_are_your_capabilities_ack(self, hostname: str, obj: RobotCapabilitiesAck):
        log.info("received what are your capabilities ack")
        if not (obj.hostname == HOSTNAME and obj.endpoint_type == self.endpoint_type):
            return
        log.info("ack was for us")
        if self._sm.listening.is_active or self._sm.greeting.is_active or self._sm.greeting_acknowledged.is_active:
            return  # too early -- haven't sent capabilities yet; a retry will arrive once we have
        self._sm.ack2_received()

    def _on_robot_start(self, hostname: str, obj: RobotStart):
        log.info("received robot start")
        if not (obj.hostname == HOSTNAME and obj.endpoint_type == self.endpoint_type):
            return
        log.info("robot start was for us")
        already_streaming = self._sm.streaming.is_active
        self._sm.chosen_received()
        if not already_streaming:
            self.root.start()

    # ------------------------------------------------------------------
    # Watchdog  (radio concern: it knows the control timestamp)
    # ------------------------------------------------------------------

    def update_last_ctrl(self):
        self._last_ctrl = time.monotonic()

    async def _watchdog(self):
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if (
                    self.is_streaming()
                    and time.monotonic() - self._last_ctrl > WATCHDOG_TIMEOUT
            ):
                log.warning("watchdog timeout -- halting")
                self.root.stop()
                self._radio_connected = False
                self._sm.stop_received()  # allow discovery, and server should know to send a RobotStart request

    async def _discovery_loop(self):
        while True:
            if self._sm.listening.is_active:
                await asyncio.sleep(0.5)
            elif self._sm.greeting.is_active:
                obj = WhoAreYou(hostname=HOSTNAME, endpoint_type=self.endpoint_type)
                self.burst(obj)
                await asyncio.sleep(0.5)
            elif self._sm.greeting_acknowledged.is_active:
                await asyncio.sleep(0.5)
            elif self._sm.explaining.is_active:
                obj = self.root.hardware.build_capabilities()
                self.burst(obj)
                await asyncio.sleep(0.5)
            elif self._sm.explaining_acknowledged.is_active:  # we are now done
                await asyncio.sleep(5.0)
            elif self._sm.streaming:
                await asyncio.sleep(5.0)

    async def _receive(self):
        all_handlers = {**self.handlers, **self.root.hardware.handlers}
        await receive_objs_encrypted(
            psk=self._psk,
            server_psk=self._server_psk,
            obj_handlers=all_handlers,
            unpack_obj_func=unpack_obj,
            rcvtimeo=1,
        )(self._dish)

    def async_loops(self) -> list:
        return [
            self._discovery_loop(),
            self._watchdog(),
            self._receive(),
        ]
