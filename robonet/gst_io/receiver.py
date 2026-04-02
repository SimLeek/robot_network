"""
robonet/gst_receiver.py — shared GStreamer receiver (server and robot).

Server: decoded video frames → set_last_img callback, audio → appsink.
Robot:  video → skipped (no screen yet), audio → alsasink (direct playback).
"""

from __future__ import annotations

import hashlib
import threading
from typing import Optional, Callable, TYPE_CHECKING

import gi
gi.require_version('Gst',  '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gst, GLib

Gst.init(None)

import numpy as np

if TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem
    from robonet.endpoint.base     import RobotNode

from robonet.buffers.buffer_objects import GstStreamInfo, GstStreamInfoAck
from robonet.logging_setup import setup_logging

log = setup_logging()


def _has(name: str) -> bool:
    return Gst.ElementFactory.find(name) is not None


def _first(candidates: list) -> Optional[str]:
    for name in candidates:
        if _has(name):
            log.info(f'[gst-recv] selected: {name}')
            return name
    return None


def _pick_video_decoder(codec: str) -> Optional[str]:
    """
    Return the best available decoder element for the given codec string.
    Hardware decoders are preferred (NVIDIA > VA-API > V4L2 M2M > software).

    >>> dec = _pick_video_decoder('h264')
    >>> dec is None or isinstance(dec, str)
    True
    >>> _pick_video_decoder('__not_a_codec__') is None
    True
    """
    hw = {
        'h264': ['nvh264dec',  'vaapih264dec',  'v4l2h264dec',  'avdec_h264'],
        'h265': ['nvh265dec',  'vaapih265dec',  'v4l2h265dec',  'avdec_h265'],
        'vp8':  ['nvvp8dec',   'vaapivp8dec',   'v4l2vp8dec',   'vp8dec',  'avdec_vp8'],
        'vp9':  ['nvvp9dec',   'vaapivp9dec',   'v4l2vp9dec',   'vp9dec',  'avdec_vp9'],
    }
    dec = _first(hw.get(codec, [f'avdec_{codec}']))
    if dec is None:
        log.error(f'[gst-recv] no video decoder found for codec {codec!r} — '
                  f'install gstreamer1.0-libav or a hardware decode plugin')
    return dec


def _pick_audio_decoder(codec: str) -> Optional[str]:
    """
    Return the best available audio decoder element for the given codec string.

    >>> dec = _pick_audio_decoder('opus')
    >>> dec is None or isinstance(dec, str)
    True
    """
    candidates = {
        'opus': ['opusdec'],
        'aac':  ['avdec_aac', 'faad'],
        'mp3':  ['mpg123audiodec', 'avdec_mp3'],
        'flac': ['flacdec'],
    }
    dec = _first(candidates.get(codec, [f'avdec_{codec}']))
    if dec is None:
        log.error(f'[gst-recv] no audio decoder found for codec {codec!r} — '
                  f'install gstreamer1.0-libav or the appropriate codec plugin')
    return dec


def _rtp_depay_name(codec: str) -> str:
    return {'h264':'rtph264depay','h265':'rtph265depay',
            'vp8':'rtpvp8depay','vp9':'rtpvp9depay'}[codec]


def _rtp_audio_depay_name(codec: str) -> str:
    return {'opus':'rtpopusdepay','aac':'rtpmp4adepay',
            'mp3':'rtpmpadepay','flac':'rtpgstdepay'}.get(codec, 'rtpgstdepay')


def _srtp_key_from_psk(psk: bytes) -> bytes:
    """
    Derive a 30-byte SRTP master-key+salt from the robonet PSK using BLAKE2b.

    >>> key = _srtp_key_from_psk(b'test-psk')
    >>> len(key)
    30
    """
    return hashlib.blake2b(psk, digest_size=30).digest()


def _srtp_caps(pt: int, key: bytes) -> Gst.Caps:
    """
    Build the GstCaps required by srtpdec.

    srtpdec cannot infer the cipher suite or master key from incoming packets
    because the payload is already encrypted. The caps must be set explicitly
    before the pipeline transitions to PLAYING.
    """
    return Gst.Caps.from_string(
        f'application/x-srtp, payload=(int){pt}, '
        f'srtp-key=(buffer){key.hex()}, '
        f'srtp-cipher=(string)aes-128-icm, '
        f'srtp-auth=(string)hmac-sha1-80')


class _VideoRecvPipeline:
    """
    Builds and owns the GStreamer video receive+decode pipeline.

    Pipeline graph::

        udpsrc → capsfilter(srtp) → srtpdec → <rtpdepay>
            → [h264parse/h265parse] → <decoder>
            → videoconvert → capsfilter(BGR) → appsink

    srtpdec requires the cipher/key caps upfront because SRTP encrypts the
    payload — there is nothing in the packet header to indicate the algorithm.
    appsink emits 'new-sample' on the GLib thread; we pull BGR frames there
    and forward them to the screen callback.

    Example::

        >>> def show(img):
        ...     with lock:
        ...         last_img[:] = img

        >>> info = GstStreamInfo(video_codec="h264", video_port=5600, width=640, height=480, hostname="hi",audio_codec="opus",audio_port=5601,fps=60,sample_rate=48000)
        >>> pipe = _VideoRecvPipeline(info, srtp_key=b"123456789012345678901234567890", on_frame=show)
        >>> if pipe.build():
        ...     pass
        ...     #pipe.play()

        pipe.stop()
    """

    def __init__(self, info: GstStreamInfo, srtp_key: bytes,
                 on_frame: Callable[[np.ndarray], None]):
        self._info      = info
        self._srtp_key  = srtp_key
        self._on_frame  = on_frame
        self._pipeline: Optional[Gst.Pipeline] = None

    def build(self) -> bool:
        codec = self._info.video_codec
        dec_name   = _pick_video_decoder(codec)
        depay_name = _rtp_depay_name(codec)
        if dec_name is None:
            return False

        p       = Gst.Pipeline.new('video-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'vsrc')
        capsflt = Gst.ElementFactory.make('capsfilter',   'vcaps')
        srtpdec = Gst.ElementFactory.make('srtpdec',      'vsrtpdec')
        depay   = Gst.ElementFactory.make(depay_name,     'vdepay')
        dec     = Gst.ElementFactory.make(dec_name,       'vdec')
        conv    = Gst.ElementFactory.make('videoconvert', 'vconv')
        outcaps = Gst.ElementFactory.make('capsfilter',   'voutcaps')
        sink    = Gst.ElementFactory.make('appsink',      'vsink')

        if None in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            log.error('[gst-recv] could not instantiate all video recv elements — '
                      'check gst-plugins-bad and gst-plugin-srtp are installed')
            return False

        src.set_property('port', self._info.video_port)
        capsflt.set_property('caps', _srtp_caps(96, self._srtp_key))
        outcaps.set_property('caps', Gst.Caps.from_string('video/x-raw,format=BGR'))

        # Drop stale frames rather than buffering them; we only want the latest.
        sink.set_property('emit-signals', True)
        sink.set_property('max-buffers',  2)
        sink.set_property('drop',         True)
        sink.set_property('sync',         False)
        sink.connect('new-sample', self._pull_frame,
                     self._info.width, self._info.height)

        for el in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            p.add(el)

        src.link(capsflt)
        capsflt.link(srtpdec)
        srtpdec.link(depay)

        if codec in ('h264', 'h265'):
            parse = Gst.ElementFactory.make(f'{codec}parse', 'vparse')
            p.add(parse)
            depay.link(parse)
            parse.link(dec)
        else:
            depay.link(dec)

        dec.link(conv)
        conv.link(outcaps)
        outcaps.link(sink)

        self._pipeline = p
        return True

    def _pull_frame(self, sink, width, height) -> Gst.FlowReturn:
        sample = sink.emit('pull-sample')
        if sample is None:
            log.warning('[gst-recv] pull-sample returned None')
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            log.warning('[gst-recv] buffer map failed')
            return Gst.FlowReturn.ERROR
        try:
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
            if arr.size == width * height * 3:
                self._on_frame(arr.reshape((height, width, 3)).copy())
            else:
                log.warning(f'[gst-recv] unexpected frame size {arr.size} '
                            f'(expected {width * height * 3})')
        except Exception as e:
            log.error(f'[gst-recv] frame callback error: {e}')
        finally:
            buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def play(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None


class _AudioRecvPipeline:
    """
    Builds and owns the GStreamer audio receive+decode pipeline.

    Pipeline graph::

        udpsrc → capsfilter(srtp) → srtpdec → <rtpdepay> → <decoder>
            → audioconvert → capsfilter(raw) → alsasink   (direct_audio=True)
                                             → appsink    (direct_audio=False)

    direct_audio=True routes decoded audio straight to an ALSA device for
    immediate playback (robot speaker). direct_audio=False feeds an appsink
    for future Python-side processing (server-side AI audio, etc.).

    Example::

        # Robot — play incoming audio on hardware speaker:
        >>> info = GstStreamInfo(video_codec="h264", video_port=5600, width=640, height=480, hostname="hi",audio_codec="opus",audio_port=5601,fps=60,sample_rate=48000)
        >>> pipe = _AudioRecvPipeline(info, srtp_key=b"123456789012345678901234567890",
        ...                           direct_audio=True, audio_device='default')
        >>> if pipe.build():
        ...     pass
        ...     #pipe.play()

        # Server — capture for processing:
        >>> pipe = _AudioRecvPipeline(info, srtp_key=b"123456789012345678901234567890",
        ...                           direct_audio=False, audio_device='default')
        >>> if pipe.build():
        ...     pass
        ...     #pipe.play()
    """

    def __init__(self, info: GstStreamInfo, srtp_key: bytes,
                 direct_audio: bool, audio_device: str,
                 on_audio: Optional[Callable[[np.ndarray], None]] = None):
        self._info         = info
        self._srtp_key     = srtp_key
        self._direct_audio = direct_audio
        self._audio_device = audio_device
        self._pipeline:    Optional[Gst.Pipeline] = None
        self._on_audio = on_audio

    def build(self) -> bool:
        codec      = self._info.audio_codec
        dec_name   = _pick_audio_decoder(codec)
        depay_name = _rtp_audio_depay_name(codec)
        if dec_name is None:
            return False

        p       = Gst.Pipeline.new('audio-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'asrc')
        capsflt = Gst.ElementFactory.make('capsfilter',   'acaps')
        srtpdec = Gst.ElementFactory.make('srtpdec',      'asrtpdec')
        depay   = Gst.ElementFactory.make(depay_name,     'adepay')
        dec     = Gst.ElementFactory.make(dec_name,       'adec')
        conv    = Gst.ElementFactory.make('audioconvert', 'aconv')
        outcaps = Gst.ElementFactory.make('capsfilter',   'aoutcaps')

        if self._direct_audio:
            sink = Gst.ElementFactory.make('alsasink', 'asink')
            if sink:
                sink.set_property('device', self._audio_device)
                sink.set_property('sync',   False)
        else:
            sink = Gst.ElementFactory.make('appsink', 'asink')
            if sink:
                sink.set_property('emit-signals', True)
                sink.set_property('max-buffers',  4)
                sink.set_property('drop',         True)
                sink.set_property('sync',         False)
                if self._on_audio is not None:
                    sink.connect('new-sample', self._pull_chunk)

        if None in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            log.error('[gst-recv] could not instantiate all audio recv elements — '
                      'check gst-plugins-bad and gst-plugin-srtp are installed')
            return False

        src.set_property('port', self._info.audio_port)
        capsflt.set_property('caps', _srtp_caps(97, self._srtp_key))
        outcaps.set_property('caps', Gst.Caps.from_string(
            f'audio/x-raw,rate={self._info.sample_rate},channels=1'))

        for el in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            p.add(el)
        src.link(capsflt)
        capsflt.link(srtpdec)
        srtpdec.link(depay)
        depay.link(dec)
        dec.link(conv)
        conv.link(outcaps)
        outcaps.link(sink)

        self._pipeline = p
        return True

    def _pull_chunk(self, sink) -> Gst.FlowReturn:
        sample = sink.emit('pull-sample')
        if sample is None:
            log.warning('[gst-recv] audio pull-sample returned None')
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            log.warning('[gst-recv] audio buffer map failed')
            return Gst.FlowReturn.ERROR
        try:
            # F32LE from audioconvert — one channel, sample_rate samples/sec
            chunk = np.frombuffer(mapinfo.data, dtype=np.float32).copy()
            self._on_audio(chunk)
        except Exception as e:
            log.error(f'[gst-recv] audio callback error: {e}')
        finally:
            buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def play(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None


class GstReceiver:
    """
    Shared GStreamer receiver for server and robot.

    Listens for GstStreamInfo control messages over the robonet channel,
    acks them, then builds matching receive pipelines. Re-announces are
    handled by re-acking without restarting if the config is unchanged.

    Server usage::

        receiver = GstReceiver(
            psk=open('/path/to/psk', 'rb').read(),
            set_last_img=lambda img: setattr(menu, 'last_img', img),
            direct_audio=False)
        receiver.setup(server_system)
        receiver.start()
        # Wire receiver.handlers into the radio dispatch table.
        receiver.stop()

    Robot usage::

        receiver = GstReceiver(
            psk=open('/path/to/psk', 'rb').read(),
            set_last_img=None,
            direct_audio=True,
            audio_output_device='default')
        receiver.setup(robot_node)
        receiver.start()
        receiver.stop()
    """

    def __init__(self,
                 psk:                bytes,
                 recv_img_callback:       Optional[Callable[[np.ndarray], None]] = None,
                 direct_audio:       bool                                   = False,
                 audio_output_device: str                                   = 'default',
                 recv_audio_callback: Optional[Callable[[np.ndarray], None]] = None):
        self._psk          = psk
        self._srtp_key     = _srtp_key_from_psk(psk)
        self._recv_image_callback = recv_img_callback
        self._direct_audio = direct_audio
        self._audio_device = audio_output_device
        self._recv_audio_callback = recv_audio_callback

        self._info:    Optional[GstStreamInfo]      = None
        self._vpipe:   Optional[_VideoRecvPipeline] = None
        self._apipe:   Optional[_AudioRecvPipeline] = None
        self._root     = None
        self._glib_loop:   Optional[GLib.MainLoop]    = None
        self._glib_thread: Optional[threading.Thread] = None

    def setup(self, root):
        self._root = root

    @property
    def handlers(self) -> dict:
        return {'GstStreamInfo': self.on_stream_info}

    def start(self):
        self._glib_loop   = GLib.MainLoop()
        self._glib_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name='gst-recv-glib')
        self._glib_thread.start()

    def stop(self):
        self._stop_pipelines()
        if self._glib_loop and self._glib_loop.is_running():
            self._glib_loop.quit()
        self._info = None

    def on_stream_info(self, hostname: str, obj: GstStreamInfo):
        same = (self._info is not None
                and obj.video_codec == self._info.video_codec
                and obj.audio_codec == self._info.audio_codec
                and obj.video_port  == self._info.video_port
                and obj.audio_port  == self._info.audio_port)

        if same and (self._vpipe or self._apipe):
            log.debug(f'[gst-recv] re-announce from {obj.hostname!r}, '
                      f'config unchanged — re-acking without restart')
            self._ack(obj)
            return

        log.info(f'[gst-recv] stream info from {obj.hostname!r}: '
                 f'{obj.video_codec} {obj.width}x{obj.height}@{obj.fps} '
                 f'audio={obj.audio_codec}')
        self._stop_pipelines()
        self._info = obj

        if self._recv_image_callback is not None and obj.video_codec:
            self._vpipe = _VideoRecvPipeline(
                obj, self._srtp_key, self._recv_image_callback)
            if self._vpipe.build():
                self._vpipe.play()
            else:
                log.error('[gst-recv] video pipeline build failed — no video will be displayed')
                self._vpipe = None
        elif not obj.video_codec:
            log.info('[gst-recv] no video codec in stream info — skipping video pipeline')
        else:
            log.info('[gst-recv] set_last_img is None — skipping video pipeline (no screen)')

        if obj.audio_codec:
            self._apipe = _AudioRecvPipeline(
                obj, self._srtp_key, self._direct_audio, self._audio_device,
                on_audio=self._recv_audio_callback if not (self._direct_audio and self._recv_audio_callback is not None) else None)
            if self._apipe.build():
                self._apipe.play()
            else:
                log.error('[gst-recv] audio pipeline build failed — no audio will be played')
                self._apipe = None
        else:
            log.info('[gst-recv] no audio codec in stream info — skipping audio pipeline')

        self._ack(obj)

    def _ack(self, obj: GstStreamInfo):
        self._root.radio.burst(
            GstStreamInfoAck(hostname=obj.hostname,
                             endpoint_type=obj.endpoint_type))

    def _stop_pipelines(self):
        if self._vpipe:
            self._vpipe.stop()
        if self._apipe:
            self._apipe.stop()
        self._vpipe = self._apipe = None