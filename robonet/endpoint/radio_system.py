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
    listening = State(initial=True)
    greeting = State()
    greeting_acknowledged = State()
    explaining = State()
    explaining_acknowledged = State()
    streaming = State()

    who_are_you_received = listening.to(greeting)

    ack_received = greeting.to(greeting_acknowledged)

    what_are_your_capabilities_received = greeting_acknowledged.to(explaining)

    ack2_received = explaining.to(explaining_acknowledged)

    chosen_received = (explaining_acknowledged.to(streaming) | explaining.to(streaming) | greeting_acknowledged.to(
        streaming) | greeting.to(streaming) | listening.to(streaming))

    stop_received = streaming.to(listening)

    def on_transition(self, event, source, target):
        if source is not target:
            log.info("handshake: %s → %s  [%s]", source.id, target.id, event)
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

    def setup(self, parent: 'RobotNode'):
        self.root = parent
        log.info("'%s' online", HOSTNAME)

    def stop(self):
        self._radio.close()
        self._dish.close()

    def burst(self, obj):
        log.info(f"burst: {type(obj)}")
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
        current = self._sm.current_state_value

        if not self._radio_connected and obj.ip:
            self._server_ip = obj.ip
            self.root.update_server_ip(obj.ip)
            self._radio.connect(f'udp://{obj.ip}:{settings["their_port"]}')
            self._radio_connected = True
            log.info(f"[radio] learned IP {obj.ip} from WhoAreYou")
        if self._radio_connected: # no point in sending if we can't talk
            if self._sm.listening.is_active:
                self._sm.who_are_you_received()
                self.burst(WhoAreYou(hostname=HOSTNAME, endpoint_type=self.endpoint_type))
            elif self._sm.greeting.is_active:
                self.burst(WhoAreYou(hostname=HOSTNAME, endpoint_type=self.endpoint_type))
            else:
                log.error(f"WhoAreYou request received while in {current} state")


    def _on_who_are_you_ack(self, hostname: str, obj: WhoAreYouAck):
        log.info("received who are you ack")
        if not (obj.hostname == HOSTNAME and obj.endpoint_type == self.endpoint_type):
            return
        current = self._sm.current_state_value
        log.info("ack was for us")
        if self._sm.greeting.is_active:
            self._sm.ack_received()
        else:
            log.error(f"WhoAreYouAck received while in {current} state")

    def on_what_are_your_capabilities(self, hostname: str, obj: RobotCapabilities):
        log.info("Received what are your capabilities")
        current = self._sm.current_state_value

        if self._sm.greeting_acknowledged.is_active:
            self._sm.what_are_your_capabilities_received()
            obj = self.root.hardware.build_capabilities()
            obj.hostname = HOSTNAME
            obj.endpoint_type = self.endpoint_type
            self.burst(obj)
        elif self._sm.explaining.is_active:
            obj = self.root.hardware.build_capabilities()
            obj.hostname = HOSTNAME
            obj.endpoint_type = self.endpoint_type
            self.burst(obj)
        else:
            log.error(f"WhatAreYourCapabilities request received while in {current} state")

    def on_what_are_your_capabilities_ack(self, hostname: str, obj: RobotCapabilitiesAck):
        log.info("received what are your capabilities ack")
        if not (obj.hostname == HOSTNAME and obj.endpoint_type == self.endpoint_type):
            return
        current = self._sm.current_state_value
        log.info("ack was for us")
        if self._sm.explaining.is_active:
            self._sm.ack2_received()
        else:
            log.error(f"WhatAreYourCapabilitiesAck received while in {current} state")

    def _on_robot_start(self, hostname: str, obj: RobotStart):
        log.info("received robot start")
        if not (obj.hostname == HOSTNAME and obj.endpoint_type == self.endpoint_type):
            return
        self._sm.chosen_received()
        log.info("robot start was for us")
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
                log.warning("watchdog timeout — halting")
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
