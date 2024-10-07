import time

from PyV4L2Cam.camera import Camera
import struct
import cv2
import numpy as np


class CameraPack:
    def __init__(self, device='/dev/video0', width=320, height=240):
        self.camera = Camera(device, width, height)
        self.width = width
        self.height = height
        self.test_variable = b'small byte array'  # Example test variable

    def get_packed_frame(self):
        frame_bytes = self.camera.get_frame()
        a = frame_bytes.find(b'\xff\xd8')
        b = frame_bytes.find(b'\xff\xd9')
        if a != -1 and b != -1:
            jpg = frame_bytes[a:b + 2]
        else:
            jpg = None
        """Pack the camera resolution, frame length, frame bytes, and test variable into a UDP message."""
        # Convert width, height, and frame_bytes length into byte format
        if jpg is not None:
            frame_length_bytes = struct.pack('!Q', len(jpg))  # Frame length (8 bytes unsigned int)
        else:
            frame_length_bytes = struct.pack('!Q', 0)
            jpg = b''
        packed_data = frame_length_bytes + jpg

        return packed_data

    @staticmethod
    def unpack_frame(packed_data):
        """Unpack the received data into its components."""
        # Start unpacking the data
        offset = 0
        try:
            # Unpack frame length (8 bytes unsigned int)
            frame_length = struct.unpack_from('!Q', packed_data, offset)[0]
            offset += 8

            # Unpack frame bytes
            jpg_bytes = packed_data[offset:offset + frame_length]
            offset += frame_length
        except struct.error:
            jpg_bytes = b''

        return jpg_bytes

    @staticmethod
    def to_cv2_image(jpg_bytes):
        image = cv2.imdecode(np.frombuffer(jpg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        return image


if __name__ == '__main__':
    cam = CameraPack()
    while True:
        print(cam.get_packed_frame())
        time.sleep(1.0 / 120)
