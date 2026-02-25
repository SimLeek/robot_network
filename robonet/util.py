import socket
import subprocess
import time
import zmq
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
import os
import struct
import asyncio
from typing import Callable, Dict, List, Optional
import zmq.asyncio

def get_local_ip():
    """Get the local IPv4 address of the server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def get_connection_info():
    """Get Wi-Fi devices for ad hoc and current network for resetting on end."""
    try:
        result = subprocess.run(
            "nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

    try:
        current_connection = subprocess.run(
            f"nmcli -t -f GENERAL.CONNECTION device show {devices[0]} | grep -oP 'GENERAL.CONNECTION:\\K\\w+'",
            shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

    return devices, current_connection


def switch_connections(current_connection, next_connection):
    try:
        command = f"nmcli con down {current_connection}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could shut down the adhoc connection: {e.stderr}")

    try:
        command = f"nmcli con up {next_connection}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could bring up the adhoc connection: {e.stderr}")

def server_udp_discovery(ctx, local_ip):
    """Send pings to clients and discover their IP address."""
    radio = ctx.socket(zmq.RADIO)
    dish = ctx.socket(zmq.DISH)
    dish.rcvtimeo = 1000

    dish.bind("udp://239.0.0.1:9998")
    dish.join("discovery")
    radio.connect("udp://239.0.0.1:9999")

    while True:
        message = f"PING from server: {local_ip}"
        radio.send(message.encode("utf-8"), group="discovery")
        print(f"Sent: {message}")
        time.sleep(1)

        try:
            msg = dish.recv(copy=False)
            client_message = msg.bytes.decode("utf-8")
            print(f"Received {msg.group}: {client_message}")

            if "PING_RESPONSE from client" in client_message:
                client_ip = client_message.split(":")[-1].strip()
                print(f"Discovered client IP: {client_ip}")
                break
        except zmq.Again:
            print("No client response yet")

    dish.close()
    radio.close()

    return client_ip

def client_udp_discovery(ctx, local_ip):
    """Listen for pings from the server and respond with the client's IP."""
    radio = ctx.socket(zmq.RADIO)
    dish = ctx.socket(zmq.DISH)
    dish.rcvtimeo = 1000

    dish.bind('udp://239.0.0.1:9999')
    dish.join('discovery')
    radio.connect('udp://239.0.0.1:9998')

    server_ip = None
    while True:
        try:
            msg = dish.recv(copy=False)
            server_message = msg.bytes.decode('utf-8')
            print(f"Received {msg.group}: {server_message}")

            # Parse the server's IP address from the ping message
            if "PING from server" in server_message:
                server_ip = server_message.split(":")[-1].strip()
                print(f"Discovered server IP: {server_ip}")

            # Respond to the server with the client's IP address
            response_message = f"PING_RESPONSE from client: {local_ip}"
            radio.send(response_message.encode('utf-8'), group='discovery')
            print(f"Responded: {response_message}")

            # After responding, break and prepare for direct communication
            break
        except zmq.Again:
            print('No ping received from server')

    dish.close()
    radio.close()

    return server_ip

def server_unicast_communication(ctx, local_ip, client_ip, callback_loop):
        """Start unicast communication between server and client."""
        unicast_radio = ctx.socket(zmq.RADIO)
        unicast_radio.setsockopt(zmq.LINGER, 0)
        unicast_radio.setsockopt(zmq.CONFLATE, 1)
        unicast_dish = ctx.socket(zmq.DISH)
        unicast_dish.setsockopt(zmq.LINGER, 0)
        unicast_dish.setsockopt(zmq.CONFLATE, 1)
        unicast_dish.rcvtimeo = 1000

        unicast_dish.bind(f"udp://{local_ip}:9998")
        unicast_dish.join("direct")
        unicast_radio.connect(f"udp://{client_ip}:9999")

        print(f"Starting unicast communication with client at {client_ip}...")
        callback_loop(unicast_radio, unicast_dish)

        unicast_dish.close()
        unicast_radio.close()

async def client_unicast_communication(ctx, local_ip, server_ip, callback_loop):
    """Start unicast communication between client and server."""
    unicast_radio = ctx.socket(zmq.RADIO)
    unicast_radio.setsockopt(zmq.LINGER, 0)
    unicast_radio.setsockopt(zmq.CONFLATE, 1)
    unicast_dish = ctx.socket(zmq.DISH)
    unicast_dish.setsockopt(zmq.LINGER, 0)
    unicast_dish.setsockopt(zmq.CONFLATE, 1)
    unicast_dish.rcvtimeo = 1000

    unicast_dish.bind(f'udp://{local_ip}:9998')
    unicast_dish.join('direct')
    unicast_radio.connect(f'udp://{server_ip}:9999')

    print(f"Starting unicast communication with server at {server_ip}...")
    await callback_loop(unicast_radio, unicast_dish)

    unicast_dish.close()
    unicast_radio.close()


def send_burst(critical_section_lock, radio_socket, message_uid, message_parts, group='direct'):
    """Unencrypted Radio Send.
    Use this only for internal communication, such as wired or radio within a faraday cage."""
    with critical_section_lock:  # threads + asyncio...
        if len(message_parts)>1:
            # Send start part
            start_part = b"\x01" + message_uid + message_parts[0]  # start_byte, uid_byte, rest_of_bytes
            radio_socket.send(start_part, group=group)

            # Send middle parts
            for part in message_parts[1:-1]:
                middle_part = b"\x02" + message_uid + part  # middle_byte, uid_byte, rest_of_bytes
                radio_socket.send(middle_part, group=group)

            # Send end part
            end_part = b"\x03" + message_uid + message_parts[-1]  # end_byte, uid_byte, rest_of_bytes
            radio_socket.send(end_part, group=group)
        else:
            full_part = b"\x04" + message_uid + message_parts[-1]  # end_byte, uid_byte, rest_of_bytes
            radio_socket.send(full_part, group=group)

async def receive_burst(critical_section_lock, dish_socket):
    """Unencrypted Radio Receive.
    Use this only for internal communication, such as wired or radio within a faraday cage."""
    message_parts = []
    message_uid = None
    while True:
        try:
            async with critical_section_lock:  # receive a burst
                part = await dish_socket.recv()

                # Identify part type (start, middle, end)
                part_type = part[0:1]
                uid_byte = part[1:2]
                payload = part[2:]

                if part_type == b"\x01":  # start part
                    message_uid = uid_byte
                    message_parts = [payload]
                elif part_type == b"\x02" and uid_byte == message_uid:  # middle part
                    message_parts.append(payload)
                elif part_type == b"\x03" and uid_byte == message_uid:  # end part
                    message_parts.append(payload)
                    # Reconstruct and process full message
                    full_message = b''.join(message_parts)
                    print(f"Received complete message with UID {message_uid}: {full_message}")
                    return full_message, 0
                elif part_type == b'\x04':
                    message_uid = uid_byte
                    message_parts = [payload]
                    full_message = b''.join(message_parts)
                    return full_message, 0
                elif uid_byte != message_uid:
                    print("message corrupted or alternative message interleaved. part will be appended to broken message output for error handling.")
                    full_message = b''.join(message_parts)
                    return full_message, part
                else:
                    print('start byte corrupted. Exiting.')
                    full_message = b''.join(message_parts)
                    return full_message, part
        except zmq.error.Again:
            print("No message received (timeout).")
            await asyncio.sleep(0.01)

HEADER_FMT = '!BBHH'   # ctrl, uid, seq, total  → 6 bytes
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 6


def make_plain_hostname_block(hostname: str) -> bytes:
    """Encode a hostname as raw UTF-8 (no encryption)."""
    return hostname.encode('utf-8')


def wrap_packet_with_plain_hostname(hostname: str, packet: bytes) -> bytes:
    """Prepend a plain-text length-prefixed hostname block to a radio packet."""
    block = make_plain_hostname_block(hostname)
    return struct.pack('!H', len(block)) + block + packet



# ---------------------------------------------------------------------------
# PlainRadioEngine
# ---------------------------------------------------------------------------

class PlainRadioEngine:
    """
    Burst-sends and assembles multi-part unencrypted UDP messages.

    Mirrors SecureRadioEngine exactly in public API; replace AESGCM
    pack/unpack with bare struct operations.

    ctrl values: 1=first, 2=middle, 3=last, 4=single
    """

    def __init__(self, blank_callback: Optional[Callable] = None, max_sessions:int = 1):
        self.sessions: Dict[int, dict] = {}
        self.completed_queue: asyncio.Queue = asyncio.Queue()
        self.blank_callback = blank_callback or (
            lambda _seq, _sz: b'\x00' * (_sz or 4096)
        )
        self.max_sessions = max_sessions

    # ------------------------------------------------------------------
    # Packing / unpacking individual wire parts
    # ------------------------------------------------------------------

    @staticmethod
    def _pack_part(ctrl: int, uid: int, seq: int, total: int,
                   payload: bytes) -> bytes:
        """Pack a single wire part — no nonce, no encryption."""
        return struct.pack(HEADER_FMT, ctrl, uid, seq, total) + payload

    @staticmethod
    def _unpack_part(packet: bytes) -> Optional[dict]:
        """Unpack a single wire part. Returns None on malformed input."""
        if len(packet) < HEADER_SIZE:
            return None
        ctrl, uid, seq, total = struct.unpack(HEADER_FMT, packet[:HEADER_SIZE])
        return {
            'ctrl': ctrl,
            'uid': uid,
            'seq': seq,
            'total': total,
            'data': packet[HEADER_SIZE:],
        }

    # ------------------------------------------------------------------
    # Session assembly (identical logic to SecureRadioEngine)
    # ------------------------------------------------------------------

    async def cleanup_loop(self, interval: float = 0.05, timeout: float = 0.05):
        """Periodically flush stale incomplete sessions."""
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            stale = [u for u, s in self.sessions.items()
                     if now - s['last_seen'] > timeout]
            for u in stale:
                result = self._finalize(u)
                if result:
                    await self.completed_queue.put((result, u))

    def _finalize(self, uid: int) -> Optional[tuple]:
        session = self.sessions.pop(uid, None)
        if not session or not session['parts']:
            return None
        parts = session['parts']
        total = session['total']
        host = session['host']
        inferred_size = len(next(iter(parts.values())))
        hi = (total - 1) if total > 0 else max(parts)
        return host, b''.join(
            parts.get(i, self.blank_callback(i, inferred_size))
            for i in range(hi + 1)
        )

    def process_raw_packet(self, raw: bytes, hostname=None):
        """
        Unpack and accumulate one wire packet.
        Returns (assembled_payload, uid) when a message is complete,
        otherwise (None, None).

        Mirrors SecureRadioEngine.process_raw_packet exactly.
        """
        p = self._unpack_part(raw)
        if not p:
            return None, None
        uid, seq, total = p['uid'], p['seq'], p['total']
        if uid not in self.sessions:
            while len(self.sessions) >= self.max_sessions:
                oldest = min(self.sessions, key=lambda u: self.sessions[u]['last_seen'])
                del self.sessions[oldest]
            self.sessions[uid] = {'parts': {}, 'last_seen': time.time(), 'total': total, 'host': hostname}
        s = self.sessions[uid]
        s['last_seen'] = time.time()
        s['parts'][seq] = p['data']
        if total > 0:
            s['total'] = total
        if p['ctrl'] == 4 or (s['total'] > 0 and len(s['parts']) == s['total']):
            return self._finalize(uid), uid
        return None, None

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_burst(self, lock, radio_socket, uid: int, parts: List[bytes],
                   group: str = 'direct',
                   hostname: Optional[str] = None):
        """
        Pack and send a multi-part burst.

        If hostname is provided every packet is wrapped with a plain
        length-prefixed hostname block so the receiver can identify the
        sender without inspecting packet payload.

        Mirrors SecureRadioEngine.send_burst; server_aesgcm parameter is
        absent because there is nothing to encrypt.
        """
        total = len(parts)
        with lock:
            for idx, part in enumerate(parts):
                ctrl = 4 if total == 1 else (
                    1 if idx == 0 else (3 if idx == total - 1 else 2))
                packet = self._pack_part(ctrl, uid, idx, total, part)
                if hostname:
                    packet = wrap_packet_with_plain_hostname(hostname, packet)
                radio_socket.send(packet, group=group)

# ---------------------------------------------------------------------------
# Hostname envelope (client → server only)
# ---------------------------------------------------------------------------

def _make_hostname_block(server_aesgcm: AESGCM, hostname: str) -> bytes:
    nonce = os.urandom(12)
    ct = server_aesgcm.encrypt(nonce, hostname.encode('utf-8'), None)
    return nonce + ct


def wrap_packet_with_hostname(server_aesgcm: AESGCM, hostname: str, packet: bytes) -> bytes:
    """Prepend a server-PSK-encrypted hostname block to a radio packet."""
    block = _make_hostname_block(server_aesgcm, hostname)
    return struct.pack('!H', len(block)) + block + packet


# ---------------------------------------------------------------------------
# Core radio engine
# ---------------------------------------------------------------------------

class SecureRadioEngine:
    """
    Burst-sends and assembles multi-part encrypted UDP messages.

    Each part on the wire:
        nonce(12) || AES-GCM(psk, ctrl(1)|uid(1)|seq(2)|total(2)||payload)

    ctrl values: 1=first, 2=middle, 3=last, 4=single
    """

    def __init__(self, psk: bytes, blank_callback: Optional[Callable] = None, max_sessions:int=2):
        self.aesgcm = AESGCM(psk)
        self.sessions: Dict[int, dict] = {}
        self.completed_queue: asyncio.Queue = asyncio.Queue()
        self.blank_callback = blank_callback or (
            lambda _seq, _sz: b'\x00' * (_sz or 4096)
        )
        self.max_sessions = max_sessions

    # ------------------------------------------------------------------
    # Packing / unpacking individual wire parts
    # ------------------------------------------------------------------

    def _pack_part(self, ctrl: int, uid: int, seq: int, total: int,
                   payload: bytes) -> bytes:
        nonce = os.urandom(12)
        header = struct.pack('!BBHH', ctrl, uid, seq, total)
        return nonce + self.aesgcm.encrypt(nonce, header + payload, None)

    def _unpack_part(self, packet: bytes) -> Optional[dict]:
        if len(packet) < 28:
            return None
        try:
            plain = self.aesgcm.decrypt(packet[:12], packet[12:], None)
            ctrl, uid, seq, total = struct.unpack('!BBHH', plain[:6])
            return {'ctrl': ctrl, 'uid': uid, 'seq': seq,
                    'total': total, 'data': plain[6:]}
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Session assembly
    # ------------------------------------------------------------------

    async def cleanup_loop(self, interval: float = 0.05, timeout: float = 0.05):
        """Periodically flush stale incomplete sessions."""
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            stale = [u for u, s in self.sessions.items()
                     if now - s['last_seen'] > timeout]
            for u in stale:
                payload = self._finalize(u)
                if payload:
                    await self.completed_queue.put((payload, u))

    def _finalize(self, uid: int) -> Optional[bytes]:
        session = self.sessions.pop(uid, None)
        if not session or not session['parts']:
            return None
        parts = session['parts']
        total = session['total']
        host = session['host']
        inferred_size = len(next(iter(parts.values())))
        hi = (total - 1) if total > 0 else max(parts)
        return host, b''.join(
            parts.get(i, self.blank_callback(i, inferred_size))
            for i in range(hi + 1)
        )

    def process_raw_packet(self, raw: bytes, hostname=None):
        """
        Decrypt and accumulate one wire packet.
        Returns (assembled_payload, uid) when a message is complete,
        otherwise (None, None).
        """
        p = self._unpack_part(raw)
        if not p:
            return None, None
        uid, seq, total = p['uid'], p['seq'], p['total']
        if uid not in self.sessions:
            while len(self.sessions) >= self.max_sessions:
                oldest = min(self.sessions, key=lambda u: self.sessions[u]['last_seen'])
                del self.sessions[oldest]
            self.sessions[uid] = {'parts': {}, 'last_seen': time.time(), 'total': total, 'host': hostname}
        s = self.sessions[uid]
        s['last_seen'] = time.time()
        s['parts'][seq] = p['data']
        if total > 0:
            s['total'] = total
        if p['ctrl'] == 4 or (s['total'] > 0 and len(s['parts']) == s['total']):
            return self._finalize(uid), uid
        return None, None

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_burst(self, lock, radio_socket, uid: int, parts: List[bytes],
                   group: str = 'direct',
                   server_aesgcm: Optional[AESGCM] = None,
                   hostname: Optional[str] = None):
        """
        Encrypt and send a multi-part burst.

        If server_aesgcm and hostname are provided, every packet is wrapped
        with the server-PSK-encrypted hostname block so the receiver can
        identify the sender without trying multiple data PSKs.
        """
        total = len(parts)
        with lock:
            for idx, part in enumerate(parts):
                ctrl = 4 if total == 1 else (
                    1 if idx == 0 else (3 if idx == total - 1 else 2))
                packet = self._pack_part(ctrl, uid, idx, total, part)
                if server_aesgcm and hostname:
                    packet = wrap_packet_with_hostname(server_aesgcm, hostname, packet)
                radio_socket.send(packet, group=group)