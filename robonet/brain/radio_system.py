# todo: needs testing

import asyncio
import platform
import random
import socket
import subprocess
import threading
from enum import Enum
from typing import Optional, Dict

import zmq

from robonet.brain.util.network_scanner import NetworkScanner, Endpoint
from robonet.brain.util.system_base import SubSystem

from robonet.buffers.buffer_handling import pack_obj, unpack_obj
from robonet.buffers.buffer_objects import WhoAreYou, RobotCapabilities, WhoAreYouAck, RobotCapabilitiesAck
from robonet.receive_callbacks import receive_objs_encrypted
from robonet.util import SecureRadioEngine
import robonet.brain.settings as settings_
from robonet.logging_setup import setup_logging

log = setup_logging()
settings = settings_.get()

import typing
if typing.TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem

def check_wifi_connected_linux():
    output = subprocess.check_output(["iwgetid", "-r"], shell=True).decode('utf-8').strip()
    if output:
        return output
    else:
        return False

HOSTNAME = socket.gethostname()

def check_wifi_connected():
    os_name = platform.system()
    if os_name == 'Linux':
        return check_wifi_connected_linux()
    else:
        raise NotImplementedError()

class RadioSubSystem(SubSystem):
    class NetMode(Enum):
        LOCALHOST = 1
        WIFI = 2
        ADHOC = 3
        WIRED = 4

    def __init__(self):
        self.ctx        = zmq.asyncio.Context.instance()
        self.bad_state = None

        self._scanner_task: Optional[asyncio.Task] = None  # put here so init exceptions are cleaner
        self._mode = None
        self.radio = None
        self.dish = None

        #No try except blocks here. If the files don't exist, then crash.
        with open(settings['psk_file'], 'rb') as f:
            psk = f.read()
        with open(settings['server_psk_file'], 'rb') as f:
            server_psk = f.read()

        self.psk        = psk
        self.server_psk = server_psk
        self.engine: SecureRadioEngine = SecureRadioEngine(psk)

        self.radio_lock  = threading.Lock()
        self._our_port   = settings["our_port"]
        self._their_port = settings["their_port"]

        self._endpoints: Dict[str, Endpoint] = {}
        self.our_ip = None

        self._auto_connect_priority = list(settings["auto_connect_priority"])
        if not settings["localhost_enabled"] and 'localhost' in self._auto_connect_priority:
            log.warning('[radio] localhost_enabled=false -- removing localhost from auto_connect_priority')
            self._auto_connect_priority = [m for m in self._auto_connect_priority if m != 'localhost']
        self._auto_connect_endpoint_type = settings["auto_connect_endpoint_type"]
        self._auto_connect_attempt_timeout = settings["auto_connect_attempt_timeout"]

        if self._auto_connect_priority:
            try:
                self._mode = self.NetMode[self._auto_connect_priority[0].upper()]
            except KeyError:
                log.warning(f'[radio] unknown mode in auto_connect_priority: '
                           f'{self._auto_connect_priority[0]!r}, ignoring auto-connect')
                self._auto_connect_priority = []
                self._mode = self.NetMode.ADHOC
        elif check_wifi_connected():
            self._mode = self.NetMode.WIFI
        else:
            self._mode = self.NetMode.ADHOC

        self._wired_our_ip = settings["wired_our_ip"]
        self._wired_subnet = settings["wired_subnet"]
        self._wired_iface: Optional[str] = None  # set once we've configured one, for teardown

        self._adhoc_our_ip    = settings["adhoc_our_ip"]
        self._adhoc_ssid      = settings["adhoc_ssid"]
        self._adhoc_prev_conn = None  # internal for switching back to wifi
        self._wifi_prev_conn = settings["wifi_prev_connection"]

        # Public discovery callbacks -- MenuSubSystem subscribes to these
        #self.on_endpoint_found = lambda ep: None
        #self.on_endpoint_lost  = lambda ep: None

        self._connected_ip: Optional[str] = None
        self._probed_ips: set = set()

        self._scanner = NetworkScanner(
            on_found=self._on_scanner_found,
            on_lost=self._on_scanner_lost,
            probe_port=self._their_port,
        )

        # Bind to all interfaces -- works across modes without rebind
        self.dish  = self.ctx.socket(zmq.DISH)
        self.radio = self.ctx.socket(zmq.RADIO)
        for sock in (self.dish, self.radio):
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.CONFLATE, 1)
        self.radio.setsockopt(zmq.SNDHWM, 15)
        self.dish.setsockopt(zmq.RCVHWM, 15)
        self.dish.rcvtimeo = 1
        self.dish.bind(f'udp://0.0.0.0:{self._our_port}')
        self.dish.join('direct')

        self.handlers = None
        self.root = None

        self._live_handlers: dict = {}
        self._uid=0

    def setup(self, sm: 'ServerSystem'):
        self.root = sm
        self.handlers = {
            'WhoAreYou': self._who_are_you_handler(sm),
            'RobotCapabilities': self._robot_capabilities_handler(sm),
        }

    def start(self):
        self._setup_mode(self._mode)

    def stop(self):
        if self.is_scanning:
            self._scanner_task.cancel()
        self._teardown_mode(self._mode)
        if self.radio is not None:
            self.radio.close()
        if self.dish is not None:
            self.dish.close()

    @property
    def is_scanning(self):
        return self._scanner_task and not self._scanner_task.done()

    @property
    def mode(self):
        return self._mode

    def _setup_mode(self, mode: NetMode):
        """Synchronous setup for the given mode. Called by start() and switch_mode()."""
        if mode == self.NetMode.LOCALHOST:
            self.connect_additional('127.0.0.1')
        elif mode == self.NetMode.WIFI:
            pass   # scanner handles wifi discovery
        elif mode == self.NetMode.WIRED:
            self._scanner.set_subnet(self._wired_subnet) # needed so we don't scan the wifi too
            try:
                from robonet.wired.util import (
                    find_connected_ethernet_interface, set_wired_static)
                iface = find_connected_ethernet_interface()
                if iface is None:
                    print('[radio] wired mode: no ethernet interface with a cable plugged in '
                         'yet -- still restricting scanning to the wired subnet')
                else:
                    ip = self._wired_our_ip
                    prefix = int(self._wired_subnet.split('/')[1])
                    set_wired_static(iface, ip, prefix)
                    self._wired_iface = iface
                    self._scanner.set_subnet(self._wired_subnet, iface=iface)
                    print(f'[radio] wired up on {iface} ({ip}), probing...')
            except Exception as e:
                print(f'[radio] wired setup failed: {e}')
                if self.root is not None:
                    self.root.menu.set_status(
                        'Wired setup failed -- try examples/setup_eth_server.py')
        elif mode == self.NetMode.ADHOC:
            try:
                from robonet.buffers.buffer_objects import WifiSetupInfo
                from robonet.util import get_local_ip, get_connection_info
                from robonet.adhoc.util import lazy_pirate_send_con_info, set_hotspot
                wifi = WifiSetupInfo(
                    ssid=self._adhoc_ssid,
                    server_ip=self._adhoc_our_ip,
                )
                devices, self._adhoc_prev_conn = get_connection_info()
                set_hotspot(wifi, devices)
                lazy_pirate_send_con_info(self.ctx, wifi, get_local_ip())
                # Focus scanner on the adhoc /24 subnet
                self._scanner.set_subnet(f'{self._adhoc_our_ip}/24')
                print(f'[radio] adhoc up, probing...')
            except Exception as e:
                print(f'[radio] adhoc setup failed: {e}')

    def _teardown_mode(self, mode: NetMode):
        """Synchronous teardown for the given mode."""
        if mode == self.NetMode.WIRED and self._wired_iface is not None:
            try:
                from robonet.wired.util import teardown_wired_static
                teardown_wired_static()
                self._wired_iface = None
                print('[radio] wired torn down')
            except Exception as e:
                print(f'[radio] wired teardown failed: {e}')
        elif mode == self.NetMode.ADHOC and self._adhoc_prev_conn is not None:
            try:
                from robonet.util import switch_connections
                from robonet.buffers.buffer_objects import WifiSetupInfo
                wifi = WifiSetupInfo(ssid=self._adhoc_ssid,
                                     server_ip=self._adhoc_our_ip)
                switch_connections(wifi.ssid, self._adhoc_prev_conn)
                self._adhoc_prev_conn = None
                print('[radio] adhoc torn down, previous connection restored')
            except Exception as e:
                print(f'[radio] adhoc teardown failed: {e}')

    async def switch_mode(self, mode: NetMode):
        if mode == self.NetMode.LOCALHOST and not settings["localhost_enabled"]:
            if self.root is not None:
                self.root.menu.set_status('Localhost mode is disabled (localhost_enabled=false)')
            return
        if self._mode != mode:
            await self.stop_scanner_task()
            self._teardown_mode(self._mode)
            self._disconnect_all()
            self._mode = mode
            self._setup_mode(mode)
            if mode != self.NetMode.LOCALHOST:
                await self.start_scanner_task()

    # region scanner

    def _on_scanner_found(self, ep: Endpoint):
        log.info(f"endpoint found: {ep}")
        if ep.hostname != '':  # we can't unregister IPs easily
            self.connect_additional(ep.ip)
        self.on_endpoint_found(ep)

    def _on_scanner_lost(self, ep: Endpoint):
        log.info(f"endpoint lost: {ep}")
        if ep.ip in self._probed_ips and ep.ip != self._connected_ip:
            try:
                self.radio.disconnect(f'udp://{ep.ip}:{self._their_port}')
            except Exception as e:
                log.exception(f"Could not disconnect radio: {e}")
            self._probed_ips.discard(ep.ip)
        self.on_endpoint_lost(ep)

    # endregion

    # region connection

    def _disconnect_all(self):
        log.info("disconnect all")
        for ip in list(self._probed_ips):
            try:
                self.radio.disconnect(f'udp://{ip}:{self._their_port}')
            except Exception as e:
                log.exception(f"Could not disconnect radio: {e}")
        self._probed_ips.clear()
        self._connected_ip = None

    def connect_additional(self, ip: str):
        """Add ip to ZMQ radio fan-out for WhoAreYou probing."""
        if ip not in self._probed_ips:
            self.radio.connect(f'udp://{ip}:{self._their_port}')
            self._probed_ips.add(ip)
            log.info(f'[radio] probing {ip}:{self._their_port}')

    def connect_to(self, ip: str): # todo: should be ip & topic
        """Switch active connection to ip, dropping all other probed peers."""
        self.connect_additional(ip)
        for old_ip in list(self._probed_ips):
            if old_ip != ip:
                try:
                    self.radio.disconnect(f'udp://{old_ip}:{self._their_port}')
                except Exception as e:
                    log.exception(f"radio could not disconnect: {e}")
                self._probed_ips.discard(old_ip)
        self._connected_ip = ip
        print(f'[radio] connected to {ip}:{self._their_port}')

    @property
    def is_connected(self) -> bool:
        return self._connected_ip is not None

    # endregion

    # region IO

    def burst(self, obj):
        """Pack obj and send to all currently connected peers."""
        #log.info(f"burst: {type(obj)}")
        data  = pack_obj(obj)
        parts = [data[i:i + 4096] for i in range(0, len(data), 4096)]
        self._uid = (self._uid + 1) % 256
        self.engine.send_burst(self.radio_lock, self.radio,
                               self._uid, parts)

    def enrich_endpoint(self, identifier: str, hostname: str, endpoint_type: str):
        key = (hostname or identifier) + ':' + endpoint_type

        ep = self._endpoints.get(key)
        if ep is None:
            for v in self._scanner.by_ip.values():
                if v.hostname == hostname:  # only uid we have to start with
                    #if v.endpoint_type != 'unknown' and v.endpoint_type != endpoint_type:
                    #    ep = copy(v)
                    ep = v  # should copy because immutable, probably
                    break
            if ep is None:
                log.error("Could not find endpoint with IP. Cannot communicate. Must discard.")
                return

        #if ep is not None:
        ep.hostname      = hostname or ep.hostname
        ep.endpoint_type = endpoint_type
        #else:
        #    ep = Endpoint(ip=identifier, hostname=hostname,
        #                  endpoint_type=endpoint_type, reachable=True)
        self._endpoints[key] = ep
        all_endpoints = self._endpoints | self._scanner.by_hostname | self._scanner.by_ip
        self.root.menu.set_endpoints(all_endpoints)

    def on_endpoint_found(self, ep: Endpoint):
        print('found ep')
        #self._endpoints[ep.ip] = ep
        if ep.hostname == HOSTNAME:
            if '192.168.0' in ep.ip:  # ignore vnc ips
                self.our_ip = ep.ip
        all_endpoints = self._endpoints | self._scanner.by_hostname | self._scanner.by_ip
        self.root.menu.set_endpoints(all_endpoints)

    def on_endpoint_lost(self, ep: Endpoint):
        #self._endpoints.pop(ep.ip, None)
        bad_keys = set()
        for key in self._endpoints.keys():
            if ep.hostname in key:
                bad_keys.add(key)
        for key in bad_keys:
            del self._endpoints[key]

        all_endpoints = self._endpoints | self._scanner.by_hostname | self._scanner.by_ip

        self.root.menu.set_endpoints(all_endpoints)

    def _who_are_you_handler(self, sm: 'ServerSystem'):
        def handler(hostname: str, obj: WhoAreYou):
            # it's fine if this runs multiple times
            log.info(f"topic:{hostname}, obj:{type(obj)}")
            self.enrich_endpoint(hostname, obj.hostname, obj.endpoint_type)
            self.burst(WhoAreYouAck(hostname=obj.hostname, endpoint_type=obj.endpoint_type))
        return handler

    def _robot_capabilities_handler(self, sm: 'ServerSystem'):
        def handler(hostname:str, obj: RobotCapabilities):
            # this is also fine to repeat
            log.info(f"topic:{hostname}, obj:{type(obj)}")
            key = obj.hostname + ':' + obj.endpoint_type
            ep = self._endpoints.get(key)
            if ep is None:
                # First capabilities message for this endpoint: the
                # scanner's own record still has endpoint_type='unknown'
                # at this point (it doesn't know the real type until
                # this very message), so it can only be found by
                # hostname -- enrich_endpoint already does that lookup
                # correctly and populates self._endpoints[key].
                self.enrich_endpoint(hostname, obj.hostname, obj.endpoint_type)
                ep = self._endpoints.get(key)
                if ep is None:
                    return  # enrich_endpoint already logged why
            ep.axes = obj.axes()
            ep.streams = obj.streams()
            ep.capabilities_received = True
            log.info(f"capabilities received: {ep.axes}, {ep.streams}")
            self._endpoints[key] = ep  # ensure we have the axes and streams
            all_endpoints = self._endpoints | self._scanner.by_hostname | self._scanner.by_ip
            self.root.menu.set_endpoints(all_endpoints)
            self.burst(RobotCapabilitiesAck(hostname=obj.hostname, endpoint_type=obj.endpoint_type))
            self._maybe_auto_connect(ep)
        return handler

    def _maybe_auto_connect(self, ep: Endpoint):
        """Connect to the first matching, ready endpoint automatically"""
        if not self._auto_connect_priority:
            return
        if self.root is None or self.root.active_sub is not None:
            return  # already connected to something
        if not ep.capabilities_received:
            return
        wanted = self._auto_connect_endpoint_type
        if wanted != 'any' and ep.endpoint_type != wanted:
            return
        print(f'[radio] auto-connecting to {ep.hostname or ep.ip} [{ep.endpoint_type}]')
        self.root.menu._connect(ep)

    async def transmit_who_are_you(self):
        """Probe a specific endpoint that we want to connect to."""
        probe = WhoAreYou(ip=self.our_ip if self.our_ip else '')
        self.burst(probe)

    async def transmit_capabilities(self, hostname, typename):
        """Probe a specific endpoint that we want to connect to."""
        probe = RobotCapabilities(hostname=hostname, endpoint_type=typename)
        self.burst(probe)

    async def start_scanner_task(self):
        log.info("starting scanner")
        await self.stop_scanner_task()
        self._scanner_task = asyncio.create_task(self._scanner.run())

    def get_unique_endpoints(self):
        seen_ids = set()
        unique_endpoints = []

        for ep in self._endpoints.values():
            obj_id = id(ep)  # Or ep.unique_id if your class has one
            if obj_id not in seen_ids:
                unique_endpoints.append(ep)
                seen_ids.add(obj_id)
        return unique_endpoints

    async def probe_loop(self):
        while True:
            if self.is_scanning:
                # currently there's no way to send only to a specific endpoint
                # So, for now, just send to all
                # todo: add endpoint specific transmission
                await self.transmit_who_are_you()
                # the 'any' here checks if we have endpoints that we stored with types and hostnames, that do not have axes which are returned by capabilities
                for ep in self.get_unique_endpoints():
                    if not ep.axes and not ep.streams:
                        await self.transmit_capabilities(ep.hostname, ep.endpoint_type)
                        await asyncio.sleep(0.1)

            await asyncio.sleep(1.0)

    async def stop_scanner_task(self):
        log.info("stopping scanner")
        if self._scanner_task and not self._scanner_task.done():
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass

        self._scanner_task = None

    # endregion

    def rebuild_handlers(self, sm: 'ServerSystem'):
        self._live_handlers.clear()
        self._live_handlers.update(self.handlers)
        self._live_handlers.update(sm.menu.handlers)
        if sm.active_sub:
            self._live_handlers.update(sm.active_sub.handlers)

    async def _ensure_mode_active(self, mode: NetMode):
        """Make sure we're actually in `mode`."""
        if self._mode != mode:
            await self.switch_mode(mode)
        if mode != self.NetMode.LOCALHOST and not self.is_scanning:
            await self.start_scanner_task()

    async def auto_connect_sequence_loop(self):
        """Try each mode in auto_connect_priority in turn."""
        if not self._auto_connect_priority:
            return
        for mode_name in self._auto_connect_priority:
            if self.root.active_sub is not None:
                return
            try:
                mode = self.NetMode[mode_name.upper()]
            except KeyError:
                log.warning(f'[radio] auto_connect_priority: unknown mode {mode_name!r}, skipping')
                continue
            await self._ensure_mode_active(mode)
            log.info(f'[radio] auto-connect: trying {mode_name} for up to '
                     f'{self._auto_connect_attempt_timeout:.0f}s')
            waited = 0.0
            while waited < self._auto_connect_attempt_timeout:
                if self.root.active_sub is not None:
                    return
                await asyncio.sleep(1.0)
                waited += 1.0
        log.info('[radio] auto-connect: priority list exhausted, staying in last mode')

    def async_loops(self, sm: 'ServerSystem'):
        self.rebuild_handlers(sm)
        return [ self.probe_loop(),
            self.auto_connect_sequence_loop(),
            receive_objs_encrypted(
                psk=self.psk,
                obj_handlers=self._live_handlers,
                unpack_obj_func=unpack_obj,
                rcvtimeo=1,
                server_psk=self.server_psk,
            )(self.dish),
        ]

