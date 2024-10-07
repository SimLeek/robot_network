import struct
import numpy as np
from typing import List, Optional, Tuple
import numpy.typing as npt


class WifiSetupInfo:
    """Wi-Fi info buffer object. Needs to be the same on both sides."""

    type_list = [str]  # Only string types in WifiSetupInfo

    def __init__(self, ssid: str, server_ip: str, client_ip: str, password: str = "example_password"):
        self.ssid = ssid
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.password = password

    @staticmethod
    def pack_type(value, type_index):
        """Pack the value based on the type index (in this case, it's always a string)."""
        if type_index == 0:  # String (str (s))
            encoded_value = value.encode('utf-8')
            return struct.pack('!I', len(encoded_value)) + encoded_value
        else:
            raise TypeError("Unsupported type for WifiSetupInfo")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the value based on the type index (string in this case)."""
        if type_index == 0:  # String (str)
            value_len = struct.unpack_from('!I', data, offset)[0]
            offset += 4
            value = data[offset:offset + value_len].decode('utf-8')
            return value, offset + value_len
        else:
            raise TypeError("Unsupported type for WifiSetupInfo")


class TensorBuffer:
    type_list = [List[npt.NDArray]]

    def __init__(self, tensors: List[npt.NDArray]):
        self.tensors = tensors

    @staticmethod
    def pack_type(value, type_index):
        """Pack the value based on the type index."""
        if type_index == 0:  # np.ndarray for audio data
            i = len(value)
            num_tensors = struct.pack('!I', i)
            channel_bytes = []
            for v in value:
                shape = v.shape
                flat_data = v.flatten()
                shape_packed = struct.pack(f'!{len(shape)}I', *shape)
                data_packed = flat_data.tobytes()
                channel_bytes.extend([struct.pack('!I', len(shape)), shape_packed, data_packed])
            return num_tensors + b''.join(channel_bytes)
        else:
            raise TypeError("Unsupported type for TensorBuffer")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the value based on the type index."""
        if type_index == 0:  # np.ndarray
            num_tensors = struct.unpack_from('!I', data, offset)[0]
            offset += 4
            value_arrays = []
            for i in range(num_tensors):
                shape_len = struct.unpack_from('!I', data, offset)[0]
                offset += 4
                shape = struct.unpack_from(f'!{shape_len}I', data, offset)
                offset += 4 * shape_len
                flat_size = np.prod(shape)
                flat_data = data[offset:offset + flat_size * 4]
                offset += 4 * flat_size
                value_arrays.append(np.frombuffer(flat_data, dtype=np.float32).reshape(shape))
            return value_arrays, offset
        else:
            raise TypeError("Unsupported type for TensorBuffer")


# Define the CamFrame class
class CVCamFrame:
    """Camera frame alongside other info."""

    type_list = [np.ndarray, int]  # np.ndarray and int types in CamFrame

    def __init__(self, cv_image: np.ndarray, brightness: int, exposure: int):
        self.cv_image = cv_image
        self.brightness = brightness
        self.exposure = exposure

    @staticmethod
    def pack_type(value, type_index):
        """Pack the value based on the type index."""
        if type_index == 0:  # np.ndarray
            shape = value.shape
            flat_data = value.flatten()
            shape_packed = struct.pack(f'!{len(shape)}I', *shape)
            data_packed = flat_data.tobytes()
            return struct.pack('!I', len(shape)) + shape_packed + data_packed
        elif type_index == 1:  # Integer (int)
            return struct.pack('!I', value)
        else:
            raise TypeError("Unsupported type for CamFrame")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the value based on the type index."""
        if type_index == 0:  # np.ndarray
            shape_len = struct.unpack_from('!I', data, offset)[0]
            offset += 4
            shape = struct.unpack_from(f'!{shape_len}I', data, offset)
            offset += 4 * shape_len
            flat_size = np.prod(shape)
            flat_data = np.frombuffer(data[offset:offset + flat_size], dtype=np.uint8)
            offset += flat_size
            value = flat_data.reshape(shape)
            return value, offset
        elif type_index == 1:  # Integer (int)
            value = struct.unpack_from('!I', data, offset)[0]
            return value, offset + 4
        else:
            raise TypeError("Unsupported type for CVCamFrame")

class MJpegCamFrame:
    """Camera frame alongside other info."""
    # todo: mjpeg has 8x8 ffts, which should be easy to translate directly into image pyramids, potentially on the gpu
    #  so parallelize 'opencv/modules/imgcodecs/src/grfmt_jpeg.cpp' into glsl and send the mjpeg directly into the glsl vulkan kernel
    #  mjpegs are fairly standard in cameras, so until we're making camera FPGAs, it's the fastest method
    type_list = [bytes, int]  # np.ndarray and int types in CamFrame

    def __init__(self, brightness: int, exposure: int, mjpeg: bytes):
        self.brightness = brightness
        self.exposure = exposure
        self.mjpeg = mjpeg

    @staticmethod
    def pack_type(value, type_index):
        """Pack the value based on the type index."""
        if type_index == 0:  # mjpeg is already in bytes format
            mjpg_len = struct.pack('!I', len(value))
            packed_bytes = mjpg_len + value
            return packed_bytes
        elif type_index == 1:  # Integer (int)
            return struct.pack('!I', value)
        else:
            raise TypeError("Unsupported type for CamFrame")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the value based on the type index."""
        if type_index == 0:  # np.ndarray
            bytes_len = struct.unpack_from('!I', data, offset)[0]
            offset += 4
            value = data[offset:offset+bytes_len]
            offset = offset+bytes_len
            return value, offset
        elif type_index == 1:  # Integer (int)
            value = struct.unpack_from('!I', data, offset)[0]
            return value, offset + 4
        else:
            raise TypeError("Unsupported type for MJpegCamFrame")

class AudioBuffer:
    """Buffer class to handle packing and unpacking fft audio data"""

    type_list = [List[npt.NDArray[np.complex64]], int]  # np.ndarray to store audio data

    def __init__(self, sample_rate: int = 44800, samples_per_sec:int=24, fft_data: List[npt.NDArray[np.complex64]] = None):
        self.sample_rate = sample_rate
        self.samples_per_sec = samples_per_sec
        self.fft_data = fft_data

    @staticmethod
    def pack_type(value, type_index):
        """Pack the value based on the type index."""
        if type_index == 0:  # np.ndarray for audio data
            shape = value.shape
            flat_data = value.flatten()
            shape_packed = struct.pack(f'!{len(shape)}I', *shape)
            data_packed = flat_data.tobytes()
            channel_bytes = [struct.pack('!I', len(shape)), shape_packed, data_packed]
            return b''.join(channel_bytes)
        elif type_index == 1:
            return struct.pack('!I', value)
        else:
            raise TypeError("Unsupported type for AudioBuffer")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the value based on the type index."""
        if type_index == 0:  # np.ndarray
            shape_len = struct.unpack_from('!I', data, offset)[0]
            offset += 4
            shape = struct.unpack_from(f'!{shape_len}I', data, offset)
            offset += 4 * shape_len
            flat_size = np.prod(shape)
            flat_data = data[offset:offset + flat_size * 8]
            offset += 8 * flat_size
            flat_data = flat_data[0:(len(flat_data)//8)*8]  # if we're missing data, truncate
            value_arrays = np.frombuffer(flat_data, dtype=np.complex64).reshape(shape)
            return value_arrays, offset
        elif type_index == 1:  # Integer (int)
            value = struct.unpack_from('!I', data, offset)[0]
            return value, offset + 4
        else:
            raise TypeError("Unsupported type for AudioBuffer")


class HumidityWaterBuffer:
    """Buffer class to handle packing and unpacking humidity and water sensor data."""

    type_list = [float, bool]  # Types: float for humidity, bool for water detection

    def __init__(self, humidity: float, water_detected: bool):
        self.humidity: float = humidity
        self.water_detected: bool = water_detected

    @staticmethod
    def pack_type(value, type_index):
        """Pack the value based on the type index (float or bool)."""
        if type_index == 0:  # Float (humidity)
            return struct.pack('!f', value)
        elif type_index == 1:  # Boolean (water detection)
            return struct.pack('!?', value)
        else:
            raise TypeError("Unsupported type for HumidityWaterBuffer")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the value based on the type index (float or bool)."""
        if type_index == 0:  # Float (humidity)
            value = struct.unpack_from('!f', data, offset)[0]
            return value, offset + 4
        elif type_index == 1:  # Boolean (water detection)
            value = struct.unpack_from('!?', data, offset)[0]
            return value, offset + 1
        else:
            raise TypeError("Unsupported type for HumidityWaterBuffer")


class TemperatureMonitorBuffer:
    """Buffer class to handle packing and unpacking temperature sensor data from multiple channels."""

    type_list = [List[float]]  # Only a list of floats (for temperature readings)

    def __init__(self, temperature_readings: List[float]):
        self.temperature_readings = temperature_readings

    @staticmethod
    def pack_type(value, type_index):
        """Pack the list of temperature readings."""
        if type_index == 0:  # List of floats (temperatures)
            packed_data = struct.pack('!I', len(value))  # Pack the list length
            for temp in value:
                packed_data += struct.pack('!f', temp)  # Pack each float in the list
            return packed_data
        else:
            raise TypeError("Unsupported type for TemperatureMonitorBuffer")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack the list of temperature readings."""
        if type_index == 0:  # List of floats (temperatures)
            list_length = struct.unpack_from('!I', data, offset)[0]  # Unpack list length
            offset += 4
            temperatures = []
            for _ in range(list_length):
                temp = struct.unpack_from('!f', data, offset)[0]  # Unpack each float
                temperatures.append(temp)
                offset += 4
            return temperatures, offset
        else:
            raise TypeError("Unsupported type for TemperatureMonitorBuffer")


class IMUBuffer:
    """Buffer class to handle packing and unpacking accelerometer, gyroscope, and magnetometer data."""

    type_list = [Optional[Tuple[float, float, float]]]  # All fields are Optional[Tuple[float, float, float]]

    def __init__(self, accel_data: Optional[Tuple[float, float, float]] = None,
                 gyro_data: Optional[Tuple[float, float, float]] = None,
                 mag_data: Optional[Tuple[float, float, float]] = None):
        self.accel_data = accel_data
        self.gyro_data = gyro_data
        self.mag_data = mag_data

    @staticmethod
    def pack_type(value, type_index) -> bytes:
        """Pack some sensor data."""
        if type_index == 0:  # pack optional tuple of 3 floats
            if value is None:
                return struct.pack('!I', 0)  # 0 means no data
            else:
                return struct.pack('!Ifff', 3, *value)  # 3 means tuple size, followed by the 3 float values
        else:
            raise TypeError(f"Unsupported type for {IMUBuffer.__name__}")

    @staticmethod
    def unpack_type(data, offset, type_index):
        """Unpack some sensor data."""
        if type_index == 0:
            length = struct.unpack_from('!I', data, offset)[0]
            offset += 4
            if length == 0:
                return None, offset  # No data
            else:
                value = struct.unpack_from('!fff', data, offset)
                offset += 12
                return value, offset
        else:
            raise TypeError(f"Unsupported type for {IMUBuffer.__name__}")
