import time

import cv2
import numpy as np
import zmq

from robonet import camera
from robonet.buffers.buffer_handling import unpack_obj
from robonet.buffers.buffer_objects import AudioBuffer

from displayarray import display
import asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import struct
from .util import SecureRadioEngine, PlainRadioEngine
from typing import Callable, Optional

def display_mjpg_cv(displayer):
    def display_mjpeg(unicast_radio, unicast_dish):
        while True:
            try:
                direct_message = f"Direct message from server"
                unicast_radio.send(direct_message.encode("utf-8"), group="direct")
                print(f"Sent: {direct_message}")

                try:
                    msg = unicast_dish.recv(copy=False)
                    msg_bytes = [msg.bytes[1:]]
                    while msg.bytes[0] == ord(b"m"):  # snd more doesn't work for udp
                        msg = unicast_dish.recv(copy=False)
                        msg_bytes.append(msg.bytes[1:])
                    msg = b"".join(msg_bytes)

                    jpg_bytes = (
                        camera.CameraPack.unpack_frame(msg)
                    )
                    img = camera.CameraPack.to_cv2_image(jpg_bytes)
                    try:
                        if img is not None and img.size > 0:
                            displayer.update(img, 'Camera Stream')
                    except cv2.error as e:
                        print(f"OpenCV error: {e}")
                except zmq.Again:
                    print("No direct message yet")
                    time.sleep(1.0 / 120)
            except KeyboardInterrupt:
                break

    return display_mjpeg

def create_pyramid(x, min_size=1):
    pyramid = [x]
    current = x
    while current.shape[0] > min_size:
        current = cv2.resize(current, (current.shape[1], current.shape[0]//2), interpolation=cv2.INTER_LINEAR)
        pyramid.append(current)
    return pyramid

def display_fftnet(displayer):
    def fft_to_nnet(obj:AudioBuffer):
        fft_size = obj.fft_data.shape[0]*2
        fft_mag = np.abs(obj.fft_data) / (fft_size // 2)

        # magnify lower amplitudes
        fft_mag = np.sqrt(fft_mag)  # sounddevice sets mag to -1 to 1, so sqrt is fine
        # this should go through an edge detector, just like vision

        fft_phase = np.angle(obj.fft_data)  # -pi to pi phase
        # this should go through a convolution, but maybe not an edge detector, so it needs to learn

        fft_parts = obj.fft_data[..., np.newaxis].view(np.float32)  # -1 to 1 complex plane
        # also through conv, learned
        # sqrt(a^2+b^2), atan2(a, b), and back aren't easy functions for a neural net to learn, so this is useful info

        fft_list = []
        for i in range(fft_mag.shape[1]):
            fft_list.append(fft_mag[:, i])
            fft_list.append(fft_phase[:, i])
            fft_list.append(fft_parts[:, i, 0])
            fft_list.append(fft_parts[:, i, 1])

        full_fft = np.stack(fft_list, axis=-1)

        fft_pyr = create_pyramid(full_fft)

        for e, fft_p in enumerate(fft_pyr):
            displayer.update(fft_p, f'fft {e}')

    return fft_to_nnet

class MessageHandler:
    def __init__(self, handle_byte_obj):
        self.state = self.wait_for_start  # Set initial state as the wait_for_start function
        self.message_uid = None
        self.message_parts = []
        self.handle_byte_obj = handle_byte_obj

    def reset(self):
        """Reset the state machine to the initial state."""
        self.state = self.wait_for_start
        self.message_uid = None
        self.message_parts = []

    def transition(self, msg):
        """Call the current state's handler."""
        self.state(msg)

    def wait_for_start(self, msg):
        """Handles the initial state waiting for the start part."""
        part_type = msg[0:1]
        uid_byte = msg[1:2]
        payload = msg[2:]

        if part_type == b'\x01':  # Start part
            self.message_uid = uid_byte
            self.message_parts = [payload]
            self.state = self.receive_parts  # Transition to RECEIVE_PARTS state
            return True  # block
        elif part_type == b'\x04':  # Tiny message
            self.handle_byte_obj(msg[2:])
            self.reset()
            return False  # non-block
        else:
            print("Start byte corrupted or missing. Dropping message.")
            self.reset()
            return False  # non-block

    def receive_parts(self, msg):
        """Handles receiving parts of a message."""
        part_type = msg[0:1]
        uid_byte = msg[1:2]
        payload = msg[2:]

        if uid_byte == self.message_uid:
            if part_type == b'\x02':  # Middle part
                self.message_parts.append(payload)
                return True  # block
            elif part_type == b'\x03':  # End part
                self.message_parts.append(payload)
                full_message = b''.join(self.message_parts)
                self.handle_byte_obj(full_message)
                self.reset()
                return False  # non-block
            elif part_type == b'\x01':  # New message, part missed
                print('New multi-part message received in the middle of another. Handling what we have.')
                full_message = b''.join(self.message_parts)
                self.handle_byte_obj(full_message)
                self.message_uid = uid_byte
                self.message_parts = [payload]
                return True  # block
            elif part_type == b'\x04':  # Tiny message (end missed)
                print('New single-part message received in the middle of another. Handling what we have.')
                full_message = b''.join(self.message_parts)
                self.handle_byte_obj(full_message)
                self.handle_byte_obj(msg[1:])
                self.reset()
                return False  # non-block
        else:
            print('Messages corrupted or interleaved. Handling what we have.')
            self.handle_message_corruption()

    def handle_message_corruption(self):
        """Handles corrupted messages."""
        full_message = b''.join(self.message_parts)
        self.handle_byte_obj(full_message)
        self.reset()
        return False  # non-block

def unwrap_plain_hostname_from_packet(raw: bytes):
    """
    Strip the plain hostname prefix added by wrap_packet_with_plain_hostname.
    Returns (hostname: str | None, remainder: bytes).
    Returns (None, raw) if the packet is too short or malformed.
    """
    if len(raw) < 2:
        return None, raw
    block_len = struct.unpack('!H', raw[:2])[0]
    if len(raw) < 2 + block_len:
        return None, raw
    try:
        hostname = raw[2:2 + block_len].decode('utf-8')
    except UnicodeDecodeError:
        return None, raw
    return hostname, raw[2 + block_len:]

def receive_objs(
    obj_handlers: dict,
    unpack_obj_func: Callable,
    blank_callback: Optional[Callable] = None,
    rcvtimeo: int = 10,
    on_hostname: Optional[Callable[[str], None]] = None,
):
    """
    Returns a coroutine ``receive_some_obj(dish_socket)`` that continuously
    reads from *dish_socket*, assembles multi-part messages, and dispatches
    objects.

    on_hostname: optional callback(hostname: str) called each time a sender
                 is identified via the plain hostname prefix.

    Mirrors receive_objs_encrypted; the only structural difference is that
    there is no PSK / AESGCM argument.
    """

    def _dispatch(payload: tuple):
        hostname, data = payload
        if on_hostname and hostname:
            on_hostname(hostname)
        try:
            obj = unpack_obj_func(data)
        except Exception as e:
            print(f"[radio] failed to unpack object: {type(e)} - {e}")
            return
        name = obj.__class__.__name__
        handler = obj_handlers.get(name)
        if handler:
            handler(hostname, obj)
        else:
            print(f"[radio] unknown object type: {name}")

    async def receive_some_obj(dish_socket):
        engine = PlainRadioEngine(blank_callback)
        asyncio.create_task(engine.cleanup_loop())

        while True:
            # Drain the async-complete queue first (mirrors encrypted version)
            payload = None
            try:
                payload, _uid = engine.completed_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            if payload is None:
                try:
                    msg = await dish_socket.recv(copy=False)
                    raw = msg.bytes
                    hostname, raw = unwrap_plain_hostname_from_packet(raw)
                    payload, _uid = engine.process_raw_packet(raw, hostname)
                    dish_socket.rcvtimeo = rcvtimeo if payload else 0
                except zmq.Again:
                    await asyncio.sleep(0)
                    continue

            if payload:
                _dispatch(payload)
                await asyncio.sleep(0)

    return receive_some_obj

def unwrap_hostname_from_packet(server_aesgcm: 'AESGCM', raw: bytes):
    """
    Strip and decrypt the hostname prefix.
    Returns (hostname: str | None, remainder: bytes).
    Returns (None, raw) if decryption fails or packet is too short.
    """
    if len(raw) < 2:
        return None, raw
    block_len = struct.unpack('!H', raw[:2])[0]
    if len(raw) < 2 + block_len:
        return None, raw
    block = raw[2:2 + block_len]
    rest = raw[2 + block_len:]
    try:
        hostname = server_aesgcm.decrypt(block[:12], block[12:], None).decode('utf-8')
        return hostname, rest
    except Exception:
        return None, raw  # wrong key or corrupted — pass raw through unchanged

def receive_objs_encrypted(
    psk: bytes,
    obj_handlers: dict,
    unpack_obj_func: Callable,
    blank_callback: Optional[Callable] = None,
    rcvtimeo: int = 10,
    server_psk: Optional[bytes] = None,
    on_hostname: Optional[Callable[[str], None]] = None,
):
    """
    Returns a coroutine ``receive_some_obj(dish_socket)`` that continuously
    reads from *dish_socket*, decrypts, assembles, and dispatches objects.

    server_psk:   if set, each packet has a hostname prefix encrypted with
                  this key; the prefix is stripped before data decryption.
    on_hostname:  optional callback(hostname: str) called each time a sender
                  is identified.
    """
    server_aesgcm = AESGCM(server_psk) if server_psk else None

    def _dispatch(payload: bytes):
        hostname = payload[0]
        try:
            obj = unpack_obj_func(payload[1])
        except Exception as e:
            print(f"[radio] failed to receive object: {type(e)} - {e}")
            return
        name = obj.__class__.__name__
        handler = obj_handlers.get(name)
        if handler:
            handler(hostname, obj)  # handle specific sources differently
        else:
            print(f"[radio] unknown object type: {name}")

    async def receive_some_obj(dish_socket):
        engine = SecureRadioEngine(psk, blank_callback)
        asyncio.create_task(engine.cleanup_loop())
        while True:
            # Drain the async-complete queue first
            payload = None
            try:
                payload, _uid = engine.completed_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            if payload is None:
                try:
                    msg = await dish_socket.recv(copy=False)
                    raw = msg.bytes
                    hostname = None
                    if server_aesgcm:
                        hostname, raw = unwrap_hostname_from_packet(server_aesgcm, raw)
                    payload, _uid = engine.process_raw_packet(raw, hostname)
                    dish_socket.rcvtimeo = rcvtimeo if payload else 0
                except zmq.Again:
                    await asyncio.sleep(0)
                    continue

            if payload:
                _dispatch(payload)
                await asyncio.sleep(0)

    return receive_some_obj