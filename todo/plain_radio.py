"""
todo/plain_radio.py

The unencrypted counterpart to robonet/util.py's SecureRadioEngine --
confirmed unreferenced anywhere in the active RobotRadio/RadioSubSystem
path (grepped the whole repo before moving this). Moved here rather than
just deleted since the design (matching SecureRadioEngine's public API
exactly, so the two are swappable) might be worth reviving later -- see
todo/TODO.md for the full story of why this exists and why it's parked
here instead of active.

Originally robonet/util.py's send_burst/receive_burst/HEADER_FMT/
HEADER_SIZE/make_plain_topic_block/wrap_packet_with_plain_topic/
PlainRadioEngine.
"""

import asyncio
import struct
import time
from typing import Callable, Dict, List, Optional

import zmq


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


HEADER_FMT = '!BBHH'  # ctrl, uid, seq, total -- 6 bytes
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 6


def make_plain_topic_block(topic: str) -> bytes:
    """Encode a topic as raw UTF-8 (no encryption)."""
    return topic.encode('utf-8')


def wrap_packet_with_plain_topic(topic: str, packet: bytes) -> bytes:
    """Prepend a plain-text length-prefixed topic block to a radio packet."""
    block = make_plain_topic_block(topic)
    # Mirroring the encrypted version's structure: [Length][Topic][Packet]
    return struct.pack('!H', len(block)) + block + packet


# ---------------------------------------------------------------------------
# PlainRadioEngine
# ---------------------------------------------------------------------------

class PlainRadioEngine:
    """
    Burst-sends and assembles multi-part unencrypted UDP messages.
    Mirrors SecureRadioEngine exactly in public API.
    """

    def __init__(self, blank_callback: Optional[Callable] = None, max_sessions: int = 1):
        self.sessions: Dict[int, dict] = {}
        self.completed_queue: asyncio.Queue = asyncio.Queue()
        self.blank_callback = blank_callback or (
            lambda _seq, _sz: b'\x00' * (_sz or 4096)
        )
        self.max_sessions = max_sessions

    @staticmethod
    def _pack_part(ctrl: int, uid: int, seq: int, total: int, payload: bytes) -> bytes:
        return struct.pack(HEADER_FMT, ctrl, uid, seq, total) + payload

    @staticmethod
    def _unpack_part(packet: bytes) -> Optional[dict]:
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

    async def cleanup_loop(self, interval: float = 0.05, timeout: float = 0.05):
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
        topic = session['topic']  # Updated from host
        inferred_size = len(next(iter(parts.values())))
        hi = (total - 1) if total > 0 else max(parts)
        return topic, b''.join(
            parts.get(i, self.blank_callback(i, inferred_size))
            for i in range(hi + 1)
        )

    def process_raw_packet(self, raw: bytes, topic=None):
        """Unpack and accumulate one wire packet with an optional topic label."""
        p = self._unpack_part(raw)
        if not p:
            return None, None
        uid, seq, total = p['uid'], p['seq'], p['total']

        if uid not in self.sessions:
            while len(self.sessions) >= self.max_sessions:
                oldest = min(self.sessions, key=lambda u: self.sessions[u]['last_seen'])
                del self.sessions[oldest]
            self.sessions[uid] = {
                'parts': {},
                'last_seen': time.time(),
                'total': total,
                'topic': topic  # Updated from host
            }

        s = self.sessions[uid]
        s['last_seen'] = time.time()
        s['parts'][seq] = p['data']
        if total > 0:
            s['total'] = total

        if p['ctrl'] == 4 or (s['total'] > 0 and len(s['parts']) == s['total']):
            return self._finalize(uid), uid
        return None, None

    def send_burst(self, lock, radio_socket, uid: int, parts: List[bytes],
                   group: str = 'direct',
                   topic: Optional[str] = None):
        """Pack and send a multi-part burst with an optional topic prefix."""
        total = len(parts)
        with lock:
            for idx, part in enumerate(parts):
                ctrl = 4 if total == 1 else (
                    1 if idx == 0 else (3 if idx == total - 1 else 2))
                packet = self._pack_part(ctrl, uid, idx, total, part)
                if topic:
                    packet = wrap_packet_with_plain_topic(topic, packet)
                radio_socket.send(packet, group=group)
