"""
todo/plain_receive_callbacks.py

The unencrypted counterpart to robonet/receive_callbacks.py's
receive_objs_encrypted -- confirmed only ever used by
todo/run_fft_understanding.py (an exploration script, also moved here),
not anywhere in the active RobotRadio/RadioSubSystem path. See
todo/TODO.md.

Originally robonet/receive_callbacks.py's unwrap_topic_from_plain_packet
and receive_objs.
"""

import asyncio
import struct
from typing import Callable, Optional

import zmq

from todo.plain_radio import PlainRadioEngine


def unwrap_topic_from_plain_packet(raw: bytes):
    """
    Strip the plain topic prefix added by wrap_packet_with_plain_topic.
    Returns (topic: str | None, remainder: bytes).
    """
    if len(raw) < 2:
        return None, raw
    block_len = struct.unpack('!H', raw[:2])[0]
    if len(raw) < 2 + block_len:
        return None, raw
    try:
        topic = raw[2:2 + block_len].decode('utf-8')
    except UnicodeDecodeError:
        return None, raw
    return topic, raw[2 + block_len:]


def receive_objs(
        obj_handlers: dict,
        unpack_obj_func: Callable,
        blank_callback: Optional[Callable] = None,
        rcvtimeo: int = 10,
        topic: Optional[str] = None
):
    """
    Returns a coroutine that continuously reads from *dish_socket*,
    assembles multi-part messages, and dispatches objects based on topic.
    """

    def _dispatch(payload: tuple):
        # Mirroring the encrypted version: payload is (received_topic, data)
        received_topic, data = payload

        # Filter by topic if a specific one was requested
        if topic is not None and received_topic != topic:
            return

        try:
            obj = unpack_obj_func(data)
        except Exception as e:
            print(f"[radio] failed to unpack object: {type(e)} - {e}")
            return

        name = obj.__class__.__name__
        handler = obj_handlers.get(name)
        if handler:
            handler(received_topic, obj)
        else:
            print(f"[radio] unknown object type: {name}")

    async def receive_some_obj(dish_socket):
        engine = PlainRadioEngine(blank_callback)
        asyncio.create_task(engine.cleanup_loop())

        while True:
            payload = None
            try:
                payload, _uid = engine.completed_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            if payload is None:
                try:
                    msg = await dish_socket.recv(copy=False)
                    raw = msg.bytes

                    # Strip the unencrypted topic prefix
                    received_topic, raw = unwrap_topic_from_plain_packet(raw)

                    # Process via engine (e.g. reassembly)
                    payload, _uid = engine.process_raw_packet(raw, received_topic)

                    # Dynamic timeout: if we got a partial packet, wait; else don't block
                    dish_socket.rcvtimeo = rcvtimeo if payload else 0
                except zmq.Again:
                    await asyncio.sleep(0)
                    continue

            if payload:
                _dispatch(payload)
                await asyncio.sleep(0)

    return receive_some_obj
