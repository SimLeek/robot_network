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
            width_bytes = struct.pack('!I', self.width)  # Width (4 bytes unsigned int)
            height_bytes = struct.pack('!I', self.height)  # Height (4 bytes unsigned int)
            frame_length_bytes = struct.pack('!Q', len(jpg))  # Frame length (8 bytes unsigned int)
        else:
            width_bytes = struct.pack('!I', 0)
            height_bytes = struct.pack('!I', 0)
            frame_length_bytes = struct.pack('!Q', 0)
            jpg = b''
        # Add the frame bytes and test_variable to the packet
        test_var_length_bytes = struct.pack('!Q',
                                            len(self.test_variable))  # Test variable length (8 bytes unsigned int)
        packed_data = width_bytes + height_bytes + frame_length_bytes + jpg + test_var_length_bytes + self.test_variable

        return packed_data

    @staticmethod
    def unpack_frame(packed_data):
        """Unpack the received data into its components."""
        # Start unpacking the data
        offset = 0

        try:
            # Unpack width (4 bytes unsigned int)
            width = struct.unpack_from('!I', packed_data, offset)[0]
            offset += 4

            # Unpack height (4 bytes unsigned int)
            height = struct.unpack_from('!I', packed_data, offset)[0]
            offset += 4
        except struct.error:
            width = 0
            height = 0

        try:
            # Unpack frame length (8 bytes unsigned int)
            frame_length = struct.unpack_from('!Q', packed_data, offset)[0]
            offset += 8

            # Unpack frame bytes
            jpg_bytes = packed_data[offset:offset + frame_length]
            offset += frame_length
        except struct.error:
            jpg_bytes = b''

        try:
            # Unpack test variable length (8 bytes unsigned int)
            test_var_length = struct.unpack_from('!Q', packed_data, offset)[0]
            offset += 8

            # Unpack test variable
            test_variable = packed_data[offset:offset + test_var_length]
        except (struct.error, OverflowError):
            test_variable = b''

        return width, height, jpg_bytes, test_variable

    @staticmethod
    def to_cv2_image(jpg_bytes):
        image = cv2.imdecode(np.frombuffer(jpg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        return image


if __name__ == '__main__':
    cam = CameraPack()
    while True:
        print(cam.get_packed_frame())
        time.sleep(1.0 / 120)
