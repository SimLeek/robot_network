"""
buffer_objects.py

All wire message types for robonet / crodec.

Design
------
Every class inherits BufferBase and declares two class variables:

    type_list     List of Python types, one per logical field category.
                  The buffer_handling framework uses this to dispatch
                  pack_type / unpack_type by type_index.

    field_codecs  List of (pack_fn, unpack_fn) pairs, indexed the same
                  way as type_list.  BufferBase.pack_type / unpack_type
                  just index into this list — no per-class dispatch needed.

Codec functions are plain module-level callables with signatures:
    pack_fn(value)              -> bytes
    unpack_fn(data, offset)     -> (value, new_offset)

Parameterised codecs (e.g. for ndarray dtype) are created by the factory
functions ndarray_codec() and ndarray_list_codec().
"""
import json
import struct
from typing import List, Optional, Tuple

import numpy as np
import numpy.typing as npt


# ============================================================
# Primitive codec functions
# ============================================================

def _pack_uint32(v):
    assert isinstance(v, (int, np.integer)) and 0 <= v <= 0xFFFFFFFF
    return struct.pack('!I', v)

def _unpack_uint32(d, o):
    return struct.unpack_from('!I', d, o)[0], o + 4

def _pack_float32(v):
    assert isinstance(v, (int, float, np.floating))
    return struct.pack('!f', v)

def _unpack_float32(d, o):
    return struct.unpack_from('!f', d, o)[0], o + 4

def _pack_bool(v):
    assert isinstance(v, (bool, np.bool_))
    return struct.pack('!?', v)

def _unpack_bool(d, o):
    return struct.unpack_from('!?', d, o)[0], o + 1

def _pack_str(v):
    assert isinstance(v, str)
    enc = v.encode('utf-8')
    assert len(enc) <= 0xFFFFFFFF # Length must fit in uint32
    return struct.pack('!I', len(enc)) + enc

def _unpack_str(d, o):
    n = struct.unpack_from('!I', d, o)[0]; o += 4
    return d[o:o + n].decode('utf-8'), o + n

def _pack_bytes(v):
    assert isinstance(v, (bytes, bytearray))
    assert len(v) <= 0xFFFFFFFF
    return struct.pack('!I', len(v)) + v

def _unpack_bytes(d, o):
    n = struct.unpack_from('!I', d, o)[0]; o += 4
    return d[o:o + n], o + n

def _pack_uint32_list(v):
    assert isinstance(v, (list, tuple))
    assert isinstance(v[0], (int, np.integer))
    return struct.pack('!I', len(v)) + b''.join(_pack_uint32(x) for x in v)

def _unpack_uint32_list(d, o):
    n = struct.unpack_from('!I', d, o)[0]; o += 4
    vals = []
    for _ in range(n):
        val, o = _unpack_uint32(d, o)
        vals.append(val)
    return vals, o

def _pack_float32_list(v):
    assert isinstance(v, (list, tuple))
    assert isinstance(v[0], (int, float, np.floating))
    return struct.pack('!I', len(v)) + b''.join(_pack_float32(x) for x in v)

def _unpack_float32_list(d, o):
    n = struct.unpack_from('!I', d, o)[0]; o += 4
    vals = []
    for _ in range(n):
        val, o = _unpack_float32(d, o)
        vals.append(val)
    return vals, o

def _pack_opt_float3(v):
    if v is None:
        return struct.pack('!I', 0)
    assert len(v) == 3
    return struct.pack('!Ifff', 3, *v)

def _unpack_opt_float3(d, o):
    n = struct.unpack_from('!I', d, o)[0]; o += 4
    if n == 0:
        return None, o
    assert n == 3, f"Expected 3 floats in opt_float3, got {n}"
    return struct.unpack_from('!fff', d, o), o + 12


# ============================================================
# Parameterised ndarray codecs
# ============================================================

def ndarray_codec(dtype):
    """
    Returns (pack_fn, unpack_fn) for a single numpy array of *dtype*.
    Wire format: ndim(4) | shape(4*ndim) | raw_bytes
    """
    itemsize = np.dtype(dtype).itemsize

    def pack(v):
        assert v.dtype == dtype
        shape = v.shape
        return (struct.pack('!I', len(shape))
                + struct.pack(f'!{len(shape)}I', *shape)
                + v.flatten().tobytes())

    def unpack(d, o):
        ndim = struct.unpack_from('!I', d, o)[0]
        o += 4
        shape = struct.unpack_from(f'!{ndim}I', d, o); o += 4 * ndim
        nb = int(np.prod(shape)) * itemsize
        # Truncate to valid multiple of itemsize in case of partial packet
        nb = (nb // itemsize) * itemsize
        new_arr = np.frombuffer(d[o:o + nb], dtype=dtype).reshape(shape)
        new_o = o + nb
        return new_arr, new_o

    return pack, unpack


def ndarray_list_codec(dtype):
    """
    Returns (pack_fn, unpack_fn) for a *list* of numpy arrays of *dtype*.
    Wire format: count(4) | [ndim(4) | shape(4*ndim) | raw_bytes] × count
    """
    _pack_one, _unpack_one = ndarray_codec(dtype)

    def pack(v):
        for vi in v:
            assert vi.dtype==dtype
        return b''.join([struct.pack('!I', len(v))] + [_pack_one(a) for a in v])

    def unpack(d, o):
        n = struct.unpack_from('!I', d, o)[0]; o += 4
        arrays = []
        for _ in range(n):
            arr, o = _unpack_one(d, o)
            arrays.append(arr)
        return arrays, o

    return pack, unpack


# Shared codec instances (reused across classes)
_uint32        = (_pack_uint32,      _unpack_uint32)
_float32       = (_pack_float32,     _unpack_float32)
_bool_         = (_pack_bool,        _unpack_bool)
_str_          = (_pack_str,         _unpack_str)
_bytes_        = (_pack_bytes,       _unpack_bytes)
_uint32_list   = (_pack_uint32_list, _unpack_uint32_list)
_float32_list  = (_pack_float32_list,_unpack_float32_list)
_opt_float3    = (_pack_opt_float3,  _unpack_opt_float3)

_f32_arr       = ndarray_codec(np.float32)
_u32_arr       = ndarray_codec(np.uint32)
_u8_arr        = ndarray_codec(np.uint8)
_c64_arr       = ndarray_codec(np.complex64)
_f32_arr_list  = ndarray_list_codec(np.float32)
_c64_arr_list  = ndarray_list_codec(np.complex64)
_c128_arr_list  = ndarray_list_codec(np.complex128)


# ============================================================
# Base class
# ============================================================

class BufferBase:
    """
    Inherit from this and set:
        type_list    — for the buffer_handling framework
        field_codecs — list of (pack_fn, unpack_fn), same length as
                       the number of distinct type_index values used.
    """
    type_list:    list = []
    field_codecs: list = []

    @classmethod
    def pack_type(cls, value, type_index):
        return cls.field_codecs[type_index][0](value)

    @classmethod
    def unpack_type(cls, data, offset, type_index):
        return cls.field_codecs[type_index][1](data, offset)


class _TriggerBase(BufferBase):
    """No-payload trigger message."""
    type_list    = []
    field_codecs = []

    def __init__(self): pass

    @classmethod
    def pack_type(cls, _value, _ti):   return b''

    @classmethod
    def unpack_type(cls, _d, offset, _ti): return None, offset


# ============================================================
# Network / Wi-Fi setup
# ============================================================

class WifiSetupInfo(BufferBase):
    """Wi-Fi pairing info. All four fields are strings."""
    type_list    = [str]
    field_codecs = [_str_]

    def __init__(self, ssid: str, server_ip: str, client_ip: str = "",
                 password: str = 'example_password'):
        self.ssid      = ssid
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.password  = password


# ============================================================
# Video
# ============================================================

class CVCamFrame(BufferBase):
    """Raw uint8 camera frame with brightness and exposure."""
    type_list    = [np.ndarray, int]
    field_codecs = [_u8_arr, _uint32]

    def __init__(self, cv_image: np.ndarray, brightness: int, exposure: int):
        self.cv_image   = cv_image
        self.brightness = brightness
        self.exposure   = exposure


class MJpegCamFrame(BufferBase):
    """MJPEG-compressed camera frame with brightness and exposure.

    TODO: mjpeg has 8×8 FFTs — consider translating directly into image pyramids
    on the GPU (parallelize the JPEG codec into GLSL/Vulkan).
    """
    type_list    = [bytes, int, str]
    field_codecs = [_bytes_, _uint32, _str_]

    def __init__(self, brightness: int, exposure: int, w:int, h:int, mjpeg: bytes, format:str='mjpeg'):
        self.brightness = brightness
        self.exposure   = exposure
        self.w = w  # mostly for assertions
        self.h = h
        self.mjpeg      = mjpeg
        self.format = format


# ============================================================
# Audio
# ============================================================

class AudioBuffer(BufferBase):
    """Per-channel FFT audio sent from client mic to server.
    Field 0 is a list of complex64 arrays (one per channel).
    """
    type_list    = [List[npt.NDArray[np.complex64]], int, float]
    field_codecs = [_c128_arr_list, _uint32, _float32]

    def __init__(self, sample_rate: int = 44800, samples_per_sec: float = 24.0,
                 fft_data: List[npt.NDArray[np.complex64]] = None):
        self.sample_rate     = sample_rate
        self.samples_per_sec = samples_per_sec
        self.fft_data        = fft_data or []


class SoundNpEvent(BufferBase):
    """Raw float32 audio arrays sent from server to client."""
    type_list    = [List[npt.NDArray[np.float32]], int]
    field_codecs = [_f32_arr_list, _uint32]

    def __init__(self, arrays: List[npt.NDArray[np.float32]], sample_rate: int):
        self.arrays      = arrays
        self.sample_rate = sample_rate


class SoundFFTEvent(BufferBase):
    """Per-channel FFT audio sent from server to client."""
    type_list    = [List[npt.NDArray[np.complex64]], int]
    field_codecs = [_c64_arr_list, _uint32]

    def __init__(self, fft_data: List[npt.NDArray[np.complex64]], sample_rate: int):
        self.fft_data    = fft_data
        self.sample_rate = sample_rate


# ============================================================
# General-purpose tensor
# ============================================================

class TensorBuffer(BufferBase):
    """Generic list of float32 tensors."""
    type_list    = [List[npt.NDArray]]
    field_codecs = [_f32_arr_list]

    def __init__(self, tensors: List[npt.NDArray]):
        self.tensors = tensors

class SparseVectorBuffer(BufferBase):
    """Generic list of float32 tensors."""
    type_list    = [npt.NDArray[np.uint32], npt.NDArray[np.float32]]
    field_codecs = [_u32_arr, _f32_arr]

    def __init__(self, idx: npt.NDArray[np.uint32], val:npt.NDArray[np.float32]):
        self.idx = idx
        self.val = val

# ============================================================
# Sensor buffers
# ============================================================

class HumidityWaterBuffer(BufferBase):
    """Humidity (float) and water-detection (bool) sensor."""
    type_list    = [float, bool]
    field_codecs = [_float32, _bool_]

    def __init__(self, humidity: float, water_detected: bool):
        self.humidity       = humidity
        self.water_detected = water_detected


class TemperatureMonitorBuffer(BufferBase):
    """Multi-channel temperature readings."""
    type_list    = [List[float]]
    field_codecs = [_float32_list]

    def __init__(self, temperature_readings: List[float]):
        self.temperature_readings = temperature_readings


class IMUBuffer(BufferBase):
    """Accelerometer, gyroscope, and magnetometer — each an optional (x,y,z)."""
    type_list    = [Optional[Tuple[float, float, float]]]
    field_codecs = [_opt_float3]

    def __init__(self,
                 accel_data: Optional[Tuple[float, float, float]] = None,
                 gyro_data:  Optional[Tuple[float, float, float]] = None,
                 mag_data:   Optional[Tuple[float, float, float]] = None):
        self.accel_data = accel_data
        self.gyro_data  = gyro_data
        self.mag_data   = mag_data


# ============================================================
# Desktop client — remote capture config (server → client)
# ============================================================

class SetInputCropRes(BufferBase):
    type_list    = [int, int]
    field_codecs = [_uint32, _uint32]

    def __init__(self, width: int, height: int):
        self.width = width; self.height = height


class SetInputCropCenter(BufferBase):
    type_list    = [int, int]
    field_codecs = [_uint32, _uint32]

    def __init__(self, x: int, y: int):
        self.x = x; self.y = y


class SetOutputCropRes(BufferBase):
    type_list    = [int, int]
    field_codecs = [_uint32, _uint32]

    def __init__(self, width: int, height: int):
        self.width = width; self.height = height


class SetFFTLen(BufferBase):
    type_list    = [int]
    field_codecs = [_uint32]

    def __init__(self, length: int):
        self.length = length


class SetInputMics(BufferBase):
    type_list    = [List[int]]
    field_codecs = [_uint32_list]

    def __init__(self, mics: List[int]):
        self.mics = mics


class SetInputMicChannels(BufferBase):
    type_list    = [int]
    field_codecs = [_uint32]

    def __init__(self, channels: int):
        self.channels = channels


# ============================================================
# Desktop client — input events (server → client)
# ============================================================

class KeyEvent(BufferBase):
    type_list    = [str]
    field_codecs = [_str_]

    def __init__(self, key: str):
        self.key = key


class MouseEvent(BufferBase):
    """event_type: 0=move  1=click  2=scroll"""
    type_list    = [int, int, int, int, int]
    field_codecs = [_uint32] * 5

    def __init__(self, event_type: int, x: int, y: int, button: int, delta: int):
        self.event_type = event_type
        self.x = x; self.y = y
        self.button = button; self.delta = delta


# ============================================================
# Identification
# ============================================================

class WhoAreYou(BufferBase):
    type_list    = [str]
    field_codecs = [_str_]

    def __init__(self, hostname: str = '', endpoint_type: str = 'unknown', ip:str = ''):
        # endpoint_type: 'desktop' | 'robot' | 'unknown'
        self.hostname      = hostname
        self.endpoint_type = endpoint_type
        self.ip = ip

class WhoAreYouAck(BufferBase):
    type_list = [str]
    field_codecs = [_str_]

    def __init__(self, hostname: str = '', endpoint_type: str = 'unknown'):
        self.hostname = hostname  # send their own hostname back so they know it's them
        self.endpoint_type = endpoint_type  # send their own type in case there are multiple endpoints on this host

class RobotCapabilities(BufferBase):  # noqa: F821
    """
    Robot → brain.  Describes all control axes and sensor streams.

    json_axes: JSON list of axis descriptors:
        [{"name": "forward",
          "index": 0,
          "description": "wheel forward / back",
          "keys": [[87, 1.0], [83, -1.0]],
          "neuron": 0,
          "limit_lo": -1.0,
          "limit_hi": 1.0}, ...]

    json_streams: JSON list of stream descriptors:
        [{"name": "camera",     "type": "mjpeg",
          "width": 640,         "height": 480},
         {"name": "microphone", "type": "audio_float32",
          "sample_rate": 48000, "channels": 1}]
    """
    type_list = [str]
    field_codecs = [_str_]  # noqa: F821

    def __init__(self, json_axes: str = '[]', json_streams: str = '[]',
                 hostname: str = '', endpoint_type: str = 'unknown') -> None:
        self.json_axes = json_axes
        self.json_streams = json_streams
        self.hostname = hostname
        self.endpoint_type = endpoint_type

    @staticmethod
    def build(axes: list, streams: list,
              hostname: str = '', endpoint_type:str = 'unknown') -> 'RobotCapabilities':
        return RobotCapabilities(
            json_axes=json.dumps(axes),
            json_streams=json.dumps(streams),
            hostname=hostname,
            endpoint_type=endpoint_type
        )

    def axes(self) -> list:
        return json.loads(self.json_axes)

    def streams(self) -> list:
        return json.loads(self.json_streams)

class RobotCapabilitiesAck(BufferBase):
    type_list = [str]
    field_codecs = [_str_]

    def __init__(self, hostname: str = '', endpoint_type: str = 'unknown'):
        self.hostname = hostname  # send their own hostname back so they know it's them
        self.endpoint_type = endpoint_type  # send their own type in case there are multiple endpoints on this host

class RobotStart(BufferBase):
    type_list = [str]
    field_codecs = [_str_]

    def __init__(self, hostname: str = '', endpoint_type: str = 'unknown'):
        self.hostname = hostname  # send their own hostname back so they know it's them
        self.endpoint_type = endpoint_type  # send their own type in case there are multiple endpoints on this host


class SudoRequest(BufferBase):
    """Server → client: request a sudo password via the UI."""
    type_list    = [str]
    field_codecs = [_str_]

    def __init__(self, command_description: str = ''):
        self.command_description = command_description


class SudoResponse(BufferBase):
    """Client → server: password reply for a SudoRequest."""
    type_list    = [str]
    field_codecs = [_str_]

    def __init__(self, password: str = ''):
        self.password = password

# ============================================================
# Triggers
# ============================================================

class StartTrigger(_TriggerBase): pass
class StopTrigger(_TriggerBase):  pass


class GstStreamInfo(BufferBase):
    """
    Sent by robot to describe its GStreamer pipeline.
    Robot keeps sending until it receives GstStreamInfoAck.
    """
    type_list = [str, str, str, int, int, int, int, int, int, str]
    field_codecs = [_str_, _str_, _str_, _uint32, _uint32, _uint32, _uint32, _uint32, _uint32, _str_]

    def __init__(self,
                 hostname: str,
                 video_codec: str,  # 'h264' | 'h265' | 'vp8' | 'vp9' | ''
                 audio_codec: str,  # 'opus' | 'aac'  | ''
                 video_port: int,
                 audio_port: int,
                 width: int,
                 height: int,
                 fps: int,
                 sample_rate: int,
                 endpoint_type: str = 'robot'):
        self.hostname = hostname
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.video_port = video_port
        self.audio_port = audio_port
        self.width = width
        self.height = height
        self.fps = fps
        self.sample_rate = sample_rate
        self.endpoint_type = endpoint_type


class GstStreamInfoAck(BufferBase):
    type_list = [str, str]
    field_codecs = [_str_, _str_]

    def __init__(self, hostname: str, endpoint_type: str = 'robot'):
        self.hostname = hostname
        self.endpoint_type = endpoint_type