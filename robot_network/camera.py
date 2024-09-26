import time

from PyV4L2Cam.camera import Camera
import struct

class CameraPack:
    def __init__(self, device='/dev/video0', width=320, height=240):
        self.camera = Camera(device, width, height)
        self.width = width
        self.height = height
        self.test_variable = b'small byte array'  # Example test variable

    def get_packed_frame(self):
        frame_bytes = self.camera.get_frame()
        """Pack the camera resolution, frame length, frame bytes, and test variable into a UDP message."""
        # Convert width, height, and frame_bytes length into byte format
        width_bytes = struct.pack('!I', self.width)  # Width (4 bytes unsigned int)
        height_bytes = struct.pack('!I', self.height)  # Height (4 bytes unsigned int)
        frame_length_bytes = struct.pack('!Q', len(frame_bytes))  # Frame length (8 bytes unsigned int)

        # Add the frame bytes and test_variable to the packet
        test_var_length_bytes = struct.pack('!Q',
                                            len(self.test_variable))  # Test variable length (8 bytes unsigned int)
        packed_data = width_bytes + height_bytes + frame_length_bytes + frame_bytes + test_var_length_bytes + self.test_variable

        return packed_data

    def unpack_frame(self, packed_data):
        """Unpack the received data into its components."""
        # Start unpacking the data
        offset = 0

        # Unpack width (4 bytes unsigned int)
        width = struct.unpack_from('!I', packed_data, offset)[0]
        offset += 4

        # Unpack height (4 bytes unsigned int)
        height = struct.unpack_from('!I', packed_data, offset)[0]
        offset += 4

        # Unpack frame length (8 bytes unsigned int)
        frame_length = struct.unpack_from('!Q', packed_data, offset)[0]
        offset += 8

        # Unpack frame bytes
        frame_bytes = packed_data[offset:offset + frame_length]
        offset += frame_length

        # Unpack test variable length (8 bytes unsigned int)
        test_var_length = struct.unpack_from('!Q', packed_data, offset)[0]
        offset += 8

        # Unpack test variable
        test_variable = packed_data[offset:offset + test_var_length]

        return width, height, frame_bytes, test_variable

if __name__=='__main__':
    cam = CameraPack()
    while True:
        print(cam.get_packed_frame())
        time.sleep(1.0/120)