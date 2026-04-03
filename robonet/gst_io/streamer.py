"""
robonet/endpoint/gst_stream.py — robot-side GStreamer sender.

Codec selection probes the GStreamer registry at startup, preferring hardware
encoders. Bitrate adjusts live via RTCP feedback without pipeline restarts.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from typing import Optional, TYPE_CHECKING, Union

import gi
gi.require_version('Gst',  '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gst, GLib

Gst.init(None)

if TYPE_CHECKING:
    from robonet.endpoint.base import RobotNode
    from robonet.brain.main_system import ServerSystem

from robonet.buffers.buffer_objects import GstStreamInfo, GstStreamInfoAck
from robonet.endpoint.radio_system import HOSTNAME
from robonet.logging_setup import setup_logging

log = setup_logging()

VIDEO_PORT  = 5600
AUDIO_PORT  = 5601

BITRATE_MIN     = 200_000
BITRATE_MAX     = 8_000_000
BITRATE_DEFAULT = 2_000_000
BITRATE_STEP_DN = 0.75
BITRATE_STEP_UP = 1.10

# rb-fractionlost is 0–255 (RFC 3550 §6.4.1), where 255 = 100% loss.
# 5/255 ≈ 2% packet loss is our threshold to start backing off.
RTCP_LOSS_THRESHOLD = 5
RTCP_DROP_COUNT     = 2
RTCP_GOOD_COUNT     = 10


def _has(name: str) -> bool:
    return Gst.ElementFactory.find(name) is not None


def _first(candidates: list) -> tuple:
    """
    Return (element_name, codec_id) for the first available entry in candidates.

    >>> # In a GStreamer environment with x264enc available:
    >>> # _first([('nonexistent', 'h264'), ('x264enc', 'h264')])
    >>> # ('x264enc', 'h264')
    >>> _first([('__definitely_not_real__', 'h264')])
    (None, None)
    """
    for name, codec in candidates:
        if _has(name):
            log.info(f'[gst] selected: {name}')
            return name, codec
    return None, None


def _pick_video_encoder() -> tuple:
    """
    Probe the GStreamer registry and return the best available video encoder.

    Preference order: dedicated GPU APIs → OMX (SBC hardware) → V4L2 M2M
    (kernel codec drivers, increasingly common on newer SBCs) → Intel/NVIDIA
    VA-API → software fallbacks.

    >>> enc_name, codec = _pick_video_encoder()
    >>> codec in ('h264', 'h265', 'vp8', 'vp9', None)
    True
    """
    return _first([
        ('amfh264enc',   'h264'),   # AMD AMF
        ('mfh264enc',    'h264'),   # Windows Media Foundation
        ('vtenc_h264',   'h264'),   # Apple VideoToolbox
        ('omxh264enc',   'h264'),   # OMX — RPi, Jetson legacy, Rockchip, etc.
        ('omxh265enc',   'h265'),
        ('omxvp8enc',    'vp8'),
        ('omxvp9enc',    'vp9'),
        ('v4l2h264enc',  'h264'),   # V4L2 M2M — RPi 4/5, RK3588, etc.
        ('v4l2h265enc',  'h265'),
        ('v4l2vp8enc',   'vp8'),
        ('v4l2vp9enc',   'vp9'),
        ('msdkh264enc',  'h264'),   # Intel MSDK
        ('qsvvp9enc',    'vp9'),
        ('mfvp9enc',     'vp9'),
        ('nvh264enc',    'h264'),   # NVIDIA
        ('nvh265enc',    'h265'),
        ('nvvp9enc',     'vp9'),
        ('nvv4l2vp9enc', 'vp9'),
        ('vaapih264enc', 'h264'),   # VA-API (Intel/AMD/Mesa)
        ('vaapih265enc', 'h265'),
        ('vaapivp8enc',  'vp8'),
        ('vaapivp9enc',  'vp9'),
        ('openh264enc',  'h264'),   # software — fastest first
        ('vp8enc',       'vp8'),
        ('x264enc',      'h264'),
    ])


def _pick_audio_encoder() -> tuple:
    """
    Probe the GStreamer registry and return the best available audio encoder.

    Opus is preferred over AAC for real-time use: lower latency, better
    quality at low bitrates, and free of patent concerns.

    >>> enc_name, codec = _pick_audio_encoder()
    >>> codec in ('aac', 'opus', 'mp3', 'flac', None)
    True
    """
    return _first([
        ('omxaacenc',  'aac'),    # OMX hardware AAC
        ('opusenc',    'opus'),   # low-latency, best quality/bandwidth ratio
        ('avenc_aac',  'aac'),
        ('omxmp3enc',  'mp3'),
        ('lamemp3enc', 'mp3'),
        ('flacenc',    'flac'),   # lossless — last resort, high bandwidth
    ])


def _srtp_key_from_psk(psk: bytes) -> bytes:
    """
    Derive a 30-byte SRTP master-key+salt from the robonet PSK using BLAKE2b.

    AES-128 ICM SRTP requires exactly 16 bytes key + 14 bytes salt = 30 bytes.
    Both sides derive the same value independently — no extra key exchange needed.

    >>> key = _srtp_key_from_psk(b'test-psk')
    >>> len(key)
    30
    >>> _srtp_key_from_psk(b'test-psk') == _srtp_key_from_psk(b'test-psk')
    True
    """
    return hashlib.blake2b(psk, digest_size=30).digest()


def _srtp_buf(key: bytes) -> Gst.Buffer:
    return Gst.Buffer.new_wrapped(key)


def _apply_bitrate(enc: Gst.Element, enc_name: str, bitrate: int):
    """
    Set the encoder's bitrate property.

    Each encoder family uses a different property name and unit (bps vs kbps).
    V4L2 and OMX encoders expose bitrate through a GstStructure passed to
    extra-controls rather than a plain property — this is a V4L2 kernel API
    convention rather than a GStreamer one.
    """
    v4l2_omx = ('v4l2h264enc','v4l2h265enc','v4l2vp8enc','v4l2vp9enc',
                 'omxh264enc','omxh265enc','omxvp8enc','omxvp9enc')
    kbps_enc  = ('x264enc','mfh264enc','msdkh264enc','nvh264enc','nvh265enc',
                 'amfh264enc','vaapih264enc','vaapih265enc','vtenc_h264')
    tgt_enc   = ('vp8enc','vp9enc','vaapivp8enc','vaapivp9enc',
                 'qsvvp9enc','mfvp9enc','nvvp9enc','nvv4l2vp9enc')

    if enc_name in v4l2_omx:
        ctrl = Gst.Structure.new_empty('controls')
        ctrl.set_value('video_bitrate', bitrate)
        enc.set_property('extra-controls', ctrl)
    elif enc_name in kbps_enc:
        enc.set_property('bitrate', bitrate // 1000)
    elif enc_name in tgt_enc:
        enc.set_property('target-bitrate', bitrate)
    elif enc_name == 'openh264enc':
        enc.set_property('bitrate', bitrate)
    else:
        log.warning(f'[gst] _apply_bitrate: no bitrate mapping for {enc_name!r}, skipping')


def _rtp_pay_name(codec: str) -> str:
    return {'h264':'rtph264pay','h265':'rtph265pay',
            'vp8':'rtpvp8pay','vp9':'rtpvp9pay'}[codec]


def _rtp_audio_pay_name(codec: str) -> str:
    return {'opus':'rtpopuspay','aac':'rtpmp4apay',
            'mp3':'rtpmpapay','flac':'rtpgstpay'}.get(codec, 'rtpgstpay')


class _VideoPipeline:
    """
    Builds and owns the GStreamer video encode+send pipeline.

    Pipeline graph::

        v4l2src → videoscale → capsfilter → videoconvert → <encoder>
            → [h264parse/h265parse] → <rtppay> → rtpbin → srtpenc → udpsink

    rtpbin handles RTP sequencing and maintains the RTCP session used for
    packet-loss feedback. srtpenc encrypts each RTP packet in-place using
    AES-128 ICM before handing it to udpsink. The parse step is required for
    H.264/H.265 to extract NAL units into RTP-friendly chunks before the
    payloader sees them.

    Example::

        >>> pipe = _VideoPipeline(
        ...     src_device='/dev/video0', enc_name='nvh264enc', video_codec='h264',
        ...     receiver_ip='192.168.0.10', srtp_key=b"123456789012345678901234567890",
        ...     width=640, height=480, fps=30, bitrate=2_000_000)
        >>> if pipe.build():
        ...     pass
        ...     # pipe.connect_rtcp(my_rtcp_callback)
        ...     # pipe.play()


    # later:
    pipe.set_bitrate(1_000_000)   # live adjustment, no restart needed
    pipe.stop()
    """

    def __init__(self, src_device, enc_name, video_codec,
                 receiver_ip, srtp_key, width, height, fps, bitrate):
        self._src_device  = src_device
        self._enc_name    = enc_name
        self._video_codec = video_codec
        self._receiver_ip   = receiver_ip
        self._srtp_key    = srtp_key
        self._width       = width
        self._height      = height
        self._fps         = fps
        self._bitrate     = bitrate
        self._pipeline:   Optional[Gst.Pipeline] = None
        self._enc_elem:   Optional[Gst.Element]  = None
        self._rtpbin:     Optional[Gst.Element]  = None

    def build(self) -> bool:
        p       = Gst.Pipeline.new('video-send')
        src     = Gst.ElementFactory.make('v4l2src',      'vsrc')
        scale   = Gst.ElementFactory.make('videoscale',   'vscale')
        capsflt = Gst.ElementFactory.make('capsfilter',   'vcaps')
        conv    = Gst.ElementFactory.make('videoconvert', 'vconv')
        outcaps = Gst.ElementFactory.make('capsfilter', 'voutcaps')
        enc     = Gst.ElementFactory.make(self._enc_name, 'venc')
        rtpbin  = Gst.ElementFactory.make('rtpbin',       'vrtpbin')
        pay     = Gst.ElementFactory.make(_rtp_pay_name(self._video_codec), 'vpay')
        srtp    = Gst.ElementFactory.make('srtpenc',      'vsrtp')
        sink    = Gst.ElementFactory.make('udpsink',      'vsink')

        if None in (src, scale, capsflt, conv, enc, rtpbin, pay, srtp, sink):
            log.error('[gst] could not instantiate all video pipeline elements — '
                      'check gst-plugins-bad and gst-plugin-rsrtp are installed')
            return False

        src.set_property('device', self._src_device)
        capsflt.set_property('caps', Gst.Caps.from_string(
            f'video/x-raw,width={self._width},height={self._height},'
            f'framerate={self._fps}/1'))

        outcaps.set_property('caps', Gst.Caps.from_string('video/x-raw,format=I420'))
        pay.set_property('pt', 96)

        _apply_bitrate(enc, self._enc_name, self._bitrate)

        # Embed SPS/PPS parameter sets in every keyframe so the receiver
        # can decode from any point in the stream, not just the first packet.
        if self._video_codec == 'h264':
            pay.set_property('config-interval', 1)

        srtp.set_property('key',        _srtp_buf(self._srtp_key))
        srtp.set_property('rtp-cipher', 'aes-128-icm')
        srtp.set_property('rtp-auth',   'hmac-sha1-80')
        sink.set_property('host', self._receiver_ip)
        sink.set_property('port', VIDEO_PORT)
        sink.set_property('sync', False)

        for el in (src, scale, capsflt, conv, enc, pay, srtp, sink, rtpbin):
            p.add(el)

        src.link(scale)
        scale.link(capsflt)
        capsflt.link(conv)
        conv.link(outcaps)

        if self._video_codec in ('h264', 'h265'):
            parse = Gst.ElementFactory.make(f'{self._video_codec}parse', 'vparse')
            p.add(parse)
            outcaps.link(enc)
            enc.link(parse)
            parse.link(pay)
        else:
            outcaps.link(enc)
            enc.link(pay)

        # rtpbin exposes named request pads. send_rtp_sink_0 accepts raw RTP
        # from the payloader; send_rtp_src_0 emits sequenced and timestamped RTP.
        pay.link_pads('src', rtpbin, 'send_rtp_sink_0')
        rtpbin.link_pads('send_rtp_src_0', srtp, 'rtp_sink_0')
        srtp.link_pads('rtp_src_0', sink, 'sink')

        bus = p.get_bus()
        bus.set_sync_handler(self._on_bus_sync, None)

        self._enc_elem = enc
        self._rtpbin   = rtpbin
        self._pipeline = p
        return True

    def connect_rtcp(self, callback):
        """Wire the RTCP feedback signal to callback(rtpbin, session_id, ssrc, enc)."""
        if self._rtpbin:
            self._rtpbin.connect('on-ssrc-active', callback, self._enc_elem)

    def set_bitrate(self, bitrate: int):
        self._bitrate = bitrate
        if self._enc_elem:
            _apply_bitrate(self._enc_elem, self._enc_name, bitrate)

    def play(self):
        if self._pipeline:
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] Failed to transition to PLAYING state!')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = self._enc_elem = self._rtpbin = None

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-send] pipeline error: {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-send] pipeline warning: {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-send] EOS received')
        return Gst.BusSyncReply.DROP


class _AudioPipeline:
    """
    Builds and owns the GStreamer audio encode+send pipeline.

    Pipeline graph::

        alsasrc → capsfilter → <encoder> → <rtppay> → srtpenc → udpsink

    Audio bitrate is not dynamically adjusted; the encoder's default is used
    because audio is a small fraction of total bandwidth and latency matters
    more than marginal bitrate savings.

    Example::

        >>> pipe = _AudioPipeline(
        ...     mic_device='default', enc_name='opusenc', audio_codec='opus',
        ...     server_ip='192.168.0.10', srtp_key=b"123456789012345678901234567890", sample_rate=48000)
        >>> if pipe.build():
        ...     pass
        ...     #pipe.play()

        pipe.stop()
    """

    def __init__(self, mic_device, enc_name, audio_codec,
                 server_ip, srtp_key, sample_rate):
        self._mic_device  = mic_device
        self._enc_name    = enc_name
        self._audio_codec = audio_codec
        self._server_ip   = server_ip
        self._srtp_key    = srtp_key
        self._sample_rate = sample_rate
        self._pipeline:   Optional[Gst.Pipeline] = None

    def build(self) -> bool:
        p      = Gst.Pipeline.new('audio-send')
        src    = Gst.ElementFactory.make('alsasrc',      'asrc')
        capsflt= Gst.ElementFactory.make('capsfilter',   'acaps')
        enc    = Gst.ElementFactory.make(self._enc_name, 'aenc')
        pay    = Gst.ElementFactory.make(_rtp_audio_pay_name(self._audio_codec), 'apay')
        pay.set_property('pt', 97)
        srtp   = Gst.ElementFactory.make('srtpenc',      'asrtp')
        sink   = Gst.ElementFactory.make('udpsink',      'asink')

        if None in (src, capsflt, enc, pay, srtp, sink):
            log.error('[gst] could not instantiate all audio pipeline elements — '
                      'check gst-plugins-bad and gst-plugin-srtp are installed')
            return False

        src.set_property('device', self._mic_device)
        capsflt.set_property('caps', Gst.Caps.from_string(
            f'audio/x-raw,rate={self._sample_rate},channels=1'))
        srtp.set_property('key',        _srtp_buf(self._srtp_key))
        srtp.set_property('rtp-cipher', 'aes-128-icm')
        srtp.set_property('rtp-auth',   'hmac-sha1-80')
        sink.set_property('host', self._server_ip)
        sink.set_property('port', AUDIO_PORT)
        sink.set_property('sync', False)

        for el in (src, capsflt, enc, pay, srtp, sink):
            p.add(el)
        src.link(capsflt)
        capsflt.link(enc)
        enc.link(pay)
        pay.link(srtp)
        srtp.link(sink)

        bus = p.get_bus()
        bus.set_sync_handler(self._on_bus_sync, None)
        self._pipeline = p
        return True

    def play(self):
        if self._pipeline:
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] Failed to transition to PLAYING state!')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-send] pipeline error: {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-send] pipeline warning: {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-send] EOS received')
        return Gst.BusSyncReply.DROP


class GstSender:
    """
    GStreamer sender.

    Detects available encoders, negotiates stream parameters with the server
    via robonet control messages, then streams video + audio over SRTP/RTP/UDP.
    Bitrate is adjusted live based on RTCP receiver reports

    Example::

        import asyncio
        from robonet.endpoint.gst_stream import GstSender

        sender = GstSender(
            psk=open('/path/to/psk', 'rb').read(),
            src_device='/dev/video0',
            mic_device='default',
            sample_rate=48000)

        # Inside the robot node's run():
        sender.setup(root=robot_node, server_ip='192.168.0.10')
        sender.start()   # begins announce loop; pipelines start on ack
        # ...
        sender.stop()    # sends NULL to pipelines, cancels tasks

    The sender registers one robonet handler::

        sender.handlers  # {'GstStreamInfoAck': ...}

    Wire this into hardware_system.handlers so the radio dispatch table
    picks it up after swap_subsystem.
    """

    def __init__(self, psk: bytes,
                 src_device:  Optional[str] = '/dev/video0',
                 mic_device:  str           = 'default',
                 sample_rate: int           = 48000,
                 width:       int           = 640,
                 height:      int           = 480,
                 fps:         int           = 60):
        self._psk         = psk
        self._src_device  = src_device
        self._mic_device  = mic_device
        self._sample_rate = sample_rate
        self._width       = width
        self._height      = height
        self._fps         = fps
        self._srtp_key    = _srtp_key_from_psk(psk)
        self._bitrate     = BITRATE_DEFAULT

        self._venc_name, self._video_codec = _pick_video_encoder()
        self._aenc_name, self._audio_codec = _pick_audio_encoder()

        if self._venc_name is None:
            log.warning('[gst] no video encoder found — video will not be sent')
        if self._aenc_name is None:
            log.warning('[gst] no audio encoder found — audio will not be sent')

        self._drop_streak = 0
        self._good_streak = 0

        self._vpipe: Optional[_VideoPipeline]    = None
        self._apipe: Optional[_AudioPipeline]    = None
        self._acked       = False
        self._receiver_ip:  Optional[str]          = None
        self._root:       Optional[Union['RobotNode', 'ServerSystem']]  = None
        self._announce_task: Optional[asyncio.Task]     = None
        self._glib_loop:     Optional[GLib.MainLoop]    = None
        self._glib_thread:   Optional[threading.Thread] = None

    def setup(self, root: Union['RobotNode', 'ServerSystem'], receiver_ip: str):
        self._root      = root
        self._receiver_ip = receiver_ip

    @property
    def handlers(self) -> dict:
        return {'GstStreamInfoAck': self._on_ack}

    def start(self):
        self._acked   = False
        self._glib_loop   = GLib.MainLoop()
        self._glib_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name='gst-glib')
        self._glib_thread.start()
        self._announce_task = self._root.loop.create_task(self._announce_loop())

    def stop(self):
        if self._announce_task:
            self._announce_task.cancel()
            self._announce_task = None
        if self._vpipe:
            self._vpipe.stop()
        if self._apipe:
            self._apipe.stop()
        if self._glib_loop and self._glib_loop.is_running():
            self._glib_loop.quit()

    def _make_stream_info(self) -> GstStreamInfo:
        return GstStreamInfo(
            hostname    = HOSTNAME,
            video_codec = self._video_codec or '',
            audio_codec = self._audio_codec or '',
            video_port  = VIDEO_PORT,
            audio_port  = AUDIO_PORT,
            width       = self._width,
            height      = self._height,
            fps         = self._fps,
            sample_rate = self._sample_rate,
        )

    async def _announce_loop(self):
        while not self._acked:
            self._root.radio.burst(self._make_stream_info())
            await asyncio.sleep(0.5)

    def _on_ack(self, hostname: str, obj: GstStreamInfoAck):
        if obj.hostname != HOSTNAME:
            log.debug(f'[gst] GstStreamInfoAck for {obj.hostname!r}, not us — ignoring')
            return
        if self._acked:
            log.debug('[gst] duplicate GstStreamInfoAck — pipelines already running')
            return
        log.info('[gst] stream acked — starting pipelines')
        self._acked = True

        if self._venc_name and self._src_device and self._receiver_ip:
            log.info(f'[gst] Video send routing to IP: {self._receiver_ip}:{VIDEO_PORT} from {self._src_device}')
            self._vpipe = _VideoPipeline(
                self._src_device, self._venc_name, self._video_codec,
                self._receiver_ip, self._srtp_key,
                self._width, self._height, self._fps, self._bitrate)
            if self._vpipe.build():
                self._vpipe.connect_rtcp(self._on_rtcp_feedback)
                self._vpipe.play()
            else:
                log.error('[gst] video pipeline build failed — no video will be sent')
                self._vpipe = None
        else:
            log.info(f'[gst] skipping video pipeline '
                     f'(enc={self._venc_name!r}, device={self._src_device!r}, '
                     f'server={self._receiver_ip!r})')

        if self._aenc_name and self._receiver_ip:
            log.info(f'[gst] Audio send routing to IP: {self._receiver_ip}:{AUDIO_PORT} from {self._mic_device}')
            self._apipe = _AudioPipeline(
                self._mic_device, self._aenc_name, self._audio_codec,
                self._receiver_ip, self._srtp_key, self._sample_rate)
            if self._apipe.build():
                self._apipe.play()
            else:
                log.error('[gst] audio pipeline build failed — no audio will be sent')
                self._apipe = None
        else:
            log.info(f'[gst] skipping audio pipeline '
                     f'(enc={self._aenc_name!r}, server={self._receiver_ip!r})')

    def _on_rtcp_feedback(self, rtpbin, session_id, ssrc, enc_elem):
        """
        Fired by rtpbin each time a RTCP Receiver Report arrives from the server.

        rb-fractionlost is a fixed-point 0–255 value representing the fraction
        of RTP packets lost since the last RR (RFC 3550 §6.4.1). We accumulate
        consecutive lossy or clean reports before acting to avoid thrashing.
        """
        session = rtpbin.emit('get-internal-session', session_id)
        if not session:
            log.warning(f'[gst] RTCP: get-internal-session({session_id}) returned None')
            return
        stats = session.get_property('stats')
        if not stats:
            log.warning('[gst] RTCP: session stats property is None')
            return
        sources = stats.get_value('source-stats')
        if not sources:
            log.warning('[gst] RTCP: source-stats not present in session stats')
            return

        for i in range(sources.n_values()):
            src = sources.get_nth(i)
            frac_lost = (src.get_value('rb-fractionlost') or 0) if src else 0
            if frac_lost > RTCP_LOSS_THRESHOLD:
                self._drop_streak += 1
                self._good_streak  = 0
            else:
                self._good_streak += 1
                self._drop_streak  = 0

        if self._drop_streak >= RTCP_DROP_COUNT:
            self._drop_streak = 0
            self._step_bitrate(BITRATE_STEP_DN)
        elif self._good_streak >= RTCP_GOOD_COUNT:
            self._good_streak = 0
            self._step_bitrate(BITRATE_STEP_UP)

    def _step_bitrate(self, factor: float):
        new = max(BITRATE_MIN, min(BITRATE_MAX, int(self._bitrate * factor)))
        if new == self._bitrate:
            log.debug(f'[gst] bitrate already at limit ({new//1000} kbps), not adjusting')
            return
        log.info(f'[gst] bitrate {"↓" if factor < 1 else "↑"} '
                 f'{self._bitrate//1000}→{new//1000} kbps')
        self._bitrate = new
        if self._vpipe:
            self._vpipe.set_bitrate(new)
        if self._acked:
            self._root.radio.burst(self._make_stream_info())