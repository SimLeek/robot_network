import time

import cv2
import numpy as np
import zmq

from robonet import camera
from robonet.buffers.buffer_handling import unpack_obj
from robonet.buffers.buffer_objects import AudioBuffer

from displayarray import display
import asyncio

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


def receive_objs(obj_handlers):
    def handle_byte_obj(msg):
        obj = unpack_obj(msg)
        if obj.__class__.__name__ in obj_handlers:
            obj_handlers[obj.__class__.__name__](obj)
        else:
            print(f"unknown obj {obj.__class__.__name__}")

    async def receive_some_obj(unicast_radio, unicast_dish):
        handler = MessageHandler(handle_byte_obj)

        while True:
            message_parts = []
            try:
                msg = unicast_dish.recv(copy=False)
                block = handler.transition(msg.bytes)
                if block:
                    unicast_dish.rcvtimeo = 10  # 100fps limiting block
                else:
                    unicast_dish.rcvtimeo = 0
                    await asyncio.sleep(0)  # Message completed. thread switch
            except zmq.Again:
                #print("No direct message yet")
                await asyncio.sleep(0) # thread switch

    return receive_some_obj

