"""
robonet/gst_io/streamer_unencrypted.py -- plain RTP sender (no SRTP).

for testing / local-network use. See todo folder for planned encrypted version.

Pipeline graphs:
    Video: v4l2src -> videoscale -> capsfilter -> videoconvert -> capsfilter(I420)
               -> <encoder> -> [parse] -> <rtppay> -> udpsink
    Audio: alsasrc -> capsfilter -> <encoder> -> <rtppay> -> udpsink
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from typing import Optional, TYPE_CHECKING, Union

import gi
gi.require_version('Gst',  '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gst, GLib, GstAudio

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

BITRATE_DEFAULT = 2_000_000


def _has(name: str) -> bool:
    return Gst.ElementFactory.find(name) is not None


def _first(candidates: list) -> tuple:
    for name, codec in candidates:
        if _has(name):
            log.info(f'[gst] selected: {name}')
            return name, codec
    return None, None


def _video_encoder_candidates() -> list:
    ordered = [
        ('amfh264enc',   'h264'),
        ('mfh264enc',    'h264'),
        ('vtenc_h264',   'h264'),
        ('omxh264enc',   'h264'),
        ('omxh265enc',   'h265'),
        ('omxvp8enc',    'vp8'),
        ('omxvp9enc',    'vp9'),
        ('v4l2h264enc',  'h264'),
        ('v4l2h265enc',  'h265'),
        ('v4l2vp8enc',   'vp8'),
        ('v4l2vp9enc',   'vp9'),
        ('msdkh264enc',  'h264'),
        ('qsvvp9enc',    'vp9'),
        ('mfvp9enc',     'vp9'),
        ('nvh264enc',    'h264'),
        ('nvh265enc',    'h265'),
        ('nvvp9enc',     'vp9'),
        ('nvv4l2vp9enc', 'vp9'),
        ('vaapih264enc', 'h264'),
        ('vaapih265enc', 'h265'),
        ('vaapivp8enc',  'vp8'),
        ('vaapivp9enc',  'vp9'),
        ('openh264enc',  'h264'),
        ('vp8enc',       'vp8'),
        ('x264enc',      'h264'),
    ]
    found = [(n, c) for n, c in ordered if _has(n)]
    if not found:
        log.warning('[gst] no video encoder found in GStreamer registry')
    else:
        log.info(f'[gst] video encoder candidates: {[n for n, _ in found]}')
    return found


def _audio_encoder_candidates() -> list:
    ordered = [
        ('omxaacenc',  'aac'),
        ('opusenc',    'opus'),
        ('avenc_aac',  'aac'),
        ('omxmp3enc',  'mp3'),
        ('lamemp3enc', 'mp3'),
        ('flacenc',    'flac'),
    ]
    found = [(n, c) for n, c in ordered if _has(n)]
    if not found:
        log.warning('[gst] no audio encoder found in GStreamer registry')
    else:
        log.info(f'[gst] audio encoder candidates: {[n for n, _ in found]}')
    return found


def _apply_bitrate(enc: Gst.Element, enc_name: str, bitrate: int):
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
        log.warning(f'[gst] _apply_bitrate: no mapping for {enc_name!r}, skipping')


def _rtp_pay_name(codec: str) -> str:
    return {'h264':'rtph264pay','h265':'rtph265pay',
            'vp8':'rtpvp8pay','vp9':'rtpvp9pay'}[codec]


def _rtp_audio_pay_name(codec: str) -> str:
    return {'opus':'rtpopuspay','aac':'rtpmp4apay',
            'mp3':'rtpmpapay','flac':'rtpgstpay'}.get(codec, 'rtpgstpay')


VIDEO_SOURCE_XIMAGESRC = 'ximagesrc'


class _VideoPipeline:
    """
    Plain-RTP video encode+send pipeline (no encryption).

    v4l2src -> videoscale -> capsfilter -> videoconvert -> capsfilter(I420)
        -> <encoder> -> [h264/h265parse] -> <rtppay> -> udpsink

    src_device == VIDEO_SOURCE_XIMAGESRC captures the desktop directly
    (X11) instead of reading a v4l2 device path -- everything downstream
    (scale/convert/encode) is unchanged either way.
    """

    def __init__(self, src_device, enc_name, video_codec,
                 receiver_ip, width, height, fps, bitrate):
        self._src_device  = src_device
        self._enc_name    = enc_name
        self._video_codec = video_codec
        self._receiver_ip = receiver_ip
        self._width       = width
        self._height      = height
        self._fps         = fps
        self._bitrate     = bitrate
        self._pipeline:   Optional[Gst.Pipeline] = None
        self._enc_elem:   Optional[Gst.Element]  = None

    def build(self) -> bool:
        p = Gst.Pipeline.new('video-send')
        is_desktop = (self._src_device == VIDEO_SOURCE_XIMAGESRC)
        src     = Gst.ElementFactory.make('ximagesrc' if is_desktop else 'v4l2src', 'vsrc')
        scale   = Gst.ElementFactory.make('videoscale',   'vscale')
        capsflt = Gst.ElementFactory.make('capsfilter',   'vcaps')
        conv    = Gst.ElementFactory.make('videoconvert', 'vconv')
        outcaps = Gst.ElementFactory.make('capsfilter',   'voutcaps')
        enc     = Gst.ElementFactory.make(self._enc_name, 'venc')
        pay     = Gst.ElementFactory.make(_rtp_pay_name(self._video_codec), 'vpay')
        sink    = Gst.ElementFactory.make('udpsink',      'vsink')

        if None in (src, scale, capsflt, conv, outcaps, enc, pay, sink):
            log.error('[gst] could not instantiate all video pipeline elements')
            return False

        if is_desktop:
            src.set_property('use-damage', False)
        else:
            src.set_property('device', self._src_device)
        capsflt.set_property('caps', Gst.Caps.from_string(
            f'video/x-raw,width={self._width},height={self._height},'
            f'framerate={self._fps}/1'))
        outcaps.set_property('caps', Gst.Caps.from_string('video/x-raw,format=I420'))
        pay.set_property('pt', 96)
        if self._video_codec == 'h264':
            pay.set_property('config-interval', 1)
        _apply_bitrate(enc, self._enc_name, self._bitrate)
        sink.set_property('host', self._receiver_ip)
        sink.set_property('port', VIDEO_PORT)
        sink.set_property('sync', False)

        for el in (src, scale, capsflt, conv, outcaps, enc, pay, sink):
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

        pay.link(sink)

        self._enc_elem = enc
        self._pipeline = p
        return True

    def probe(self, timeout_s: float = 1.0) -> bool:
        if not self._pipeline:
            return False
        ret = self._pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst] {self._enc_name}: PAUSED failed')
            return False
        bus = self._pipeline.get_bus()
        msg = bus.timed_pop_filtered(int(timeout_s * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst] {self._enc_name}: probe error: {err} -- {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst] {self._enc_name}: probe warning (non-fatal): {warn}')

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst] {self._enc_name}: PLAYING failed during probe')
            return False
        msg = bus.timed_pop_filtered(int(1.0 * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        self._pipeline.set_state(Gst.State.NULL)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst] {self._enc_name}: probe test (PLAYING) error: {err} -- {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst] {self._enc_name}: probe test warning (non-fatal): {warn}')
        return True

    def play(self):
        if self._pipeline:
            bus = self._pipeline.get_bus()
            bus.set_sync_handler(self._on_bus_sync, None)
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] failed to reach PLAYING')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

    def set_bitrate(self, bitrate: int):
        self._bitrate = bitrate
        if self._enc_elem:
            _apply_bitrate(self._enc_elem, self._enc_name, bitrate)

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = self._enc_elem = None

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-send video] {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-send video] {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-send video] EOS')
        return Gst.BusSyncReply.DROP


AUDIO_SOURCE_DESKTOP_MIX = 'desktop-audio-mix'
AUDIO_SOURCE_SINE_TEST = 'sine-test-tone'


def _pactl_get_default(field: str) -> Optional[str]:
    """field: 'sink' or 'source'. Returns the current default PulseAudio/
    PipeWire-pulse device name, or None if pactl isn't available/fails."""
    try:
        out = subprocess.run(
            ['pactl', f'get-default-{field}'],
            capture_output=True, text=True, timeout=3)
        val = out.stdout.strip()
        return val or None
    except Exception:
        return None


class _AudioPipeline:
    """
    Plain-RTP audio encode+send pipeline (no encryption).

    Normal case: alsasrc -> capsfilter -> <encoder> -> <rtppay> -> udpsink

    mic_device == AUDIO_SOURCE_DESKTOP_MIX mixes the desktop's system
    audio (default sink's monitor) with its real microphone (if any)
    directly via two pulsesrc branches into an audiomixer, instead of
    needing a separate feeder writing into an ALSA loopback device.
    """

    def __init__(self, mic_device, enc_name, audio_codec,
                server_ip, sample_rate):
        self._mic_device  = mic_device
        self._enc_name    = enc_name
        self._audio_codec = audio_codec
        self._server_ip   = server_ip
        self._sample_rate = sample_rate
        self._pipeline:   Optional[Gst.Pipeline] = None

    def _build_desktop_mix_source(self, p: Gst.Pipeline) -> Optional[Gst.Element]:
        """Builds the audiomixer + pulsesrc branches, returns the mixer
        element (the thing the shared chain links from), or None if
        neither a monitor nor a mic could be found."""
        sink = _pactl_get_default('sink')
        monitor = f'{sink}.monitor' if sink else None
        mic = _pactl_get_default('source')
        if mic and monitor and mic == monitor:
            mic = None  # don't double up if "source" is the monitor itself

        mixer = Gst.ElementFactory.make('audiomixer', 'amix')
        if mixer is None:
            log.error('[gst] could not instantiate audiomixer')
            return None
        p.add(mixer)

        found_any = False
        for name, device in (('monitor', monitor), ('mic', mic)):
            if not device:
                continue
            src  = Gst.ElementFactory.make('pulsesrc',     f'a{name}src')
            conv = Gst.ElementFactory.make('audioconvert', f'a{name}conv')
            rsmp = Gst.ElementFactory.make('audioresample', f'a{name}rsmp')
            if None in (src, conv, rsmp):
                log.warning(f'[gst] could not instantiate pulsesrc branch for {name}')
                continue
            src.set_property('device', device)
            p.add(src); p.add(conv); p.add(rsmp)
            src.link(conv)
            conv.link(rsmp)
            rsmp.link(mixer)
            found_any = True

        if not found_any:
            log.error('[gst] desktop audio mix: no pulseaudio/pipewire sink or source found')
            return None
        return mixer

    def build(self) -> bool:
        p = Gst.Pipeline.new('audio-send')
        is_desktop_mix = (self._mic_device == AUDIO_SOURCE_DESKTOP_MIX)
        is_sine_test = (self._mic_device == AUDIO_SOURCE_SINE_TEST)

        capsflt = Gst.ElementFactory.make('capsfilter', 'acaps')
        enc     = Gst.ElementFactory.make(self._enc_name, 'aenc')
        pay     = Gst.ElementFactory.make(_rtp_audio_pay_name(self._audio_codec), 'apay')
        sink    = Gst.ElementFactory.make('udpsink', 'asink')
        conv    = Gst.ElementFactory.make('audioconvert', 'aconv')
        resample = Gst.ElementFactory.make('audioresample', 'aresample')

        if None in (capsflt, enc, pay, sink, conv, resample):
            log.error('[gst] could not instantiate all audio pipeline elements')
            return False

        pay.set_property('pt', 97)
        info = GstAudio.AudioInfo()
        info.set_format(GstAudio.AudioFormat.F32LE, self._sample_rate, 1)
        caps = info.to_caps()
        capsflt.set_property('caps', caps)
        #capsflt.set_property('caps', Gst.Caps.from_string(
        #    f'audio/x-raw,format=F32LE,rate={self._sample_rate},channels=1'))
        sink.set_property('host', self._server_ip)
        sink.set_property('port', AUDIO_PORT)
        sink.set_property('sync', False)

        for el in (capsflt, enc, pay, sink, conv, resample):
            p.add(el)

        # Source selection
        if is_desktop_mix:
            src_out = self._build_desktop_mix_source(p)
            if src_out is None:
                return False
        elif is_sine_test:
            src = Gst.ElementFactory.make('audiotestsrc', 'asrc')
            if src is None:
                log.error('[gst] could not instantiate audiotestsrc')
                return False
            src.set_property('wave', 'sine')
            src.set_property('freq', 440.0)
            src.set_property('is-live', True)
            p.add(src)
            src_out = src
        else:
            src = Gst.ElementFactory.make('alsasrc', 'asrc')
            if src is None:
                log.error('[gst] could not instantiate alsasrc')
                return False
            src.set_property('device', self._mic_device)
            p.add(src)
            src_out = src

        # Linking with conversion
        src_out.link(conv)
        conv.link(resample)
        resample.link(capsflt)
        capsflt.link(enc)
        enc.link(pay)
        pay.link(sink)

        self._pipeline = p
        return True

    def probe(self, timeout_s: float = 1.0) -> bool:
        if not self._pipeline:
            return False
        ret = self._pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst] {self._enc_name}: audio PAUSED failed')
            return False
        bus = self._pipeline.get_bus()
        msg = bus.timed_pop_filtered(int(timeout_s * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst] {self._enc_name}: audio probe error: {err} -- {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst] {self._enc_name}: audio probe warning (non-fatal): {warn}')

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst] {self._enc_name}: audio PLAYING failed during probe')
            return False
        msg = bus.timed_pop_filtered(int(1.0 * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        self._pipeline.set_state(Gst.State.NULL)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst] {self._enc_name}: audio probe test (PLAYING) error: {err} -- {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst] {self._enc_name}: audio probe test warning (non-fatal): {warn}')
        return True

    def play(self):
        if self._pipeline:
            bus = self._pipeline.get_bus()
            bus.set_sync_handler(self._on_bus_sync, None)
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] failed to reach PLAYING')
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
            log.error(f'[gst-send audio] {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-send audio] {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-send audio] EOS')
        return Gst.BusSyncReply.DROP


class GstSender:
    """
    Unencrypted GStreamer sender. Drop-in replacement for the SRTP version.
    """

    def __init__(self,
                 src_device:  Optional[str] = '/dev/video0',
                 mic_device:  str           = 'default',
                 sample_rate: int           = 48000,
                 width:       int           = 640,
                 height:      int           = 480,
                 fps:         int           = 60):
        self._src_device  = src_device
        self._mic_device  = mic_device
        self._sample_rate = sample_rate
        self._width       = width
        self._height      = height
        self._fps         = fps
        self._bitrate     = BITRATE_DEFAULT

        self._video_candidates = _video_encoder_candidates()
        self._audio_candidates = _audio_encoder_candidates()
        self._venc_name, self._video_codec = (
            self._video_candidates[0] if self._video_candidates else (None, None))
        self._aenc_name, self._audio_codec = (
            self._audio_candidates[0] if self._audio_candidates else (None, None))

        self._drop_streak = 0
        self._good_streak = 0

        self._vpipe: Optional[_VideoPipeline] = None
        self._apipe: Optional[_AudioPipeline] = None
        self._acked       = False
        self._receiver_ip:  Optional[str]     = None
        self._root        = None
        self._announce_task: Optional[asyncio.Task]     = None
        self._glib_loop:     Optional[GLib.MainLoop]    = None
        self._glib_thread:   Optional[threading.Thread] = None

    def setup(self, root: Union['RobotNode', 'ServerSystem'], receiver_ip: str):
        self._root        = root
        self._receiver_ip = receiver_ip

    @property
    def handlers(self) -> dict:
        return {'GstStreamInfoAck': self._on_ack}

    def start(self):
        self._acked       = False
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
            return
        if self._acked:
            log.debug('[gst] duplicate GstStreamInfoAck -- pipelines already running')
            return
        log.info('[gst] stream acked -- starting pipelines')
        self._acked = True

        if self._src_device and self._receiver_ip and self._video_candidates:
            log.info(f'[gst] video send -> {self._receiver_ip}:{VIDEO_PORT} from {self._src_device}')
            self._vpipe = self._start_video_pipeline()
            if self._vpipe is None:
                log.error('[gst] all video encoders failed probe -- no video will be sent')
        else:
            log.info(f'[gst] skipping video pipeline '
                     f'(candidates={[n for n,_ in self._video_candidates]}, '
                     f'device={self._src_device!r}, server={self._receiver_ip!r})')

        if self._receiver_ip and self._audio_candidates:
            log.info(f'[gst] audio send -> {self._receiver_ip}:{AUDIO_PORT} from {self._mic_device}')
            self._apipe = self._start_audio_pipeline()
            if self._apipe is None:
                log.error('[gst] all audio encoders failed probe -- no audio will be sent')
        else:
            log.info(f'[gst] skipping audio pipeline '
                     f'(candidates={[n for n,_ in self._audio_candidates]}, '
                     f'server={self._receiver_ip!r})')

    def _start_video_pipeline(self) -> Optional[_VideoPipeline]:
        for enc_name, codec in self._video_candidates:
            log.info(f'[gst] trying video encoder: {enc_name}')
            pipe = _VideoPipeline(
                self._src_device, enc_name, codec,
                self._receiver_ip,
                self._width, self._height, self._fps, self._bitrate)
            if not pipe.build():
                log.warning(f'[gst] {enc_name}: build failed, trying next')
                continue
            if not pipe.probe():
                log.warning(f'[gst] {enc_name}: probe failed, trying next')
                pipe.stop()
                continue
            log.info(f'[gst] video encoder selected: {enc_name} ({codec})')
            self._venc_name   = enc_name
            self._video_codec = codec
            pipe.play()
            return pipe
        return None

    def set_source_device(self, device: str):
        """Switch the v4l2 source device"""
        if device == self._src_device:
            return
        self._src_device = device
        if not self._acked:
            return  # not streaming yet -- new device will be used on start
        if self._vpipe:
            self._vpipe.stop()
            self._vpipe = None
        if self._src_device and self._receiver_ip and self._video_candidates:
            self._vpipe = self._start_video_pipeline()
            if self._vpipe is None:
                log.error('[gst] set_source_device: all video encoders failed probe')

    def set_mic_device(self, device: str):
        """Switch the ALSA mic source device"""
        if device == self._mic_device:
            return
        self._mic_device = device
        if not self._acked:
            return  # not streaming yet -- new device will be used on start
        if self._apipe:
            self._apipe.stop()
            self._apipe = None
        if self._mic_device and self._receiver_ip and self._audio_candidates:
            self._apipe = self._start_audio_pipeline()
            if self._apipe is None:
                log.error('[gst] set_mic_device: all audio encoders failed probe')

    def _start_audio_pipeline(self) -> Optional[_AudioPipeline]:
        for enc_name, codec in self._audio_candidates:
            log.info(f'[gst] trying audio encoder: {enc_name}')
            pipe = _AudioPipeline(
                self._mic_device, enc_name, codec,
                self._receiver_ip, self._sample_rate)
            if not pipe.build():
                log.warning(f'[gst] {enc_name}: build failed, trying next')
                continue
            if not pipe.probe():
                log.warning(f'[gst] {enc_name}: probe failed, trying next')
                pipe.stop()
                continue
            log.info(f'[gst] audio encoder selected: {enc_name} ({codec})')
            self._aenc_name   = enc_name
            self._audio_codec = codec
            pipe.play()
            return pipe
        return None