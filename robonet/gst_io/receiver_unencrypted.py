"""
robonet/gst_receiver_unencrypted.py — plain RTP receiver (no SRTP).

Drop-in replacement for receiver_encrypted.py for testing / local-network use.
Identical interface; just strips srtpdec and plain-RTP caps instead of x-srtp.

Pipeline graphs:
    Video: udpsrc(caps=x-rtp,pt=96) → <rtpdepay> → [parse] → <decoder>
               → videoconvert → capsfilter(BGR) → appsink
    Audio: udpsrc(caps=x-rtp,pt=97) → <rtpdepay> → <decoder>
               → audioconvert → capsfilter(raw) → alsasink / appsink
"""

from __future__ import annotations

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


def _video_decoder_candidates(codec: str) -> list[str]:
    ordered = {
        'h264': ['nvh264dec',  'vaapih264dec',  'v4l2h264dec',  'avdec_h264'],
        'h265': ['nvh265dec',  'vaapih265dec',  'v4l2h265dec',  'avdec_h265'],
        'vp8':  ['nvvp8dec',   'vaapivp8dec',   'v4l2vp8dec',   'vp8dec',  'avdec_vp8'],
        'vp9':  ['nvvp9dec',   'vaapivp9dec',   'v4l2vp9dec',   'vp9dec',  'avdec_vp9'],
    }.get(codec, [f'avdec_{codec}'])
    found = [name for name in ordered if _has(name)]
    if not found:
        log.warning(f'[gst-recv] no video decoder found for codec {codec!r}')
    else:
        log.info(f'[gst-recv] video decoder candidates for {codec}: {found}')
    return found


def _audio_decoder_candidates(codec: str) -> list[str]:
    ordered = {
        'opus': ['opusdec'],
        'aac':  ['avdec_aac', 'faad'],
        'mp3':  ['mpg123audiodec', 'avdec_mp3'],
        'flac': ['flacdec'],
    }.get(codec, [f'avdec_{codec}'])
    found = [name for name in ordered if _has(name)]
    if not found:
        log.warning(f'[gst-recv] no audio decoder found for codec {codec!r}')
    else:
        log.info(f'[gst-recv] audio decoder candidates for {codec}: {found}')
    return found


def _rtp_depay_name(codec: str) -> str:
    return {'h264':'rtph264depay','h265':'rtph265depay',
            'vp8':'rtpvp8depay','vp9':'rtpvp9depay'}[codec]


def _rtp_audio_depay_name(codec: str) -> str:
    return {'opus':'rtpopusdepay','aac':'rtpmp4adepay',
            'mp3':'rtpmpadepay','flac':'rtpgstdepay'}.get(codec, 'rtpgstdepay')


class _VideoRecvPipeline:
    """
    Plain-RTP video receive pipeline (no encryption).

    udpsrc(x-rtp,pt=96) → <rtpdepay> → [h264/h265parse] → <decoder>
        → videoconvert → capsfilter(BGR) → appsink
    """

    def __init__(self, info: GstStreamInfo, dec_name: str,
                 on_frame: Callable[[np.ndarray], None]):
        self._info     = info
        self._dec_name = dec_name
        self._on_frame = on_frame
        self._pipeline: Optional[Gst.Pipeline] = None
        self._first_packet = False

    def build(self) -> bool:
        codec      = self._info.video_codec
        depay_name = _rtp_depay_name(codec)

        p       = Gst.Pipeline.new('video-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'vsrc')
        depay   = Gst.ElementFactory.make(depay_name,     'vdepay')
        dec     = Gst.ElementFactory.make(self._dec_name, 'vdec')
        conv    = Gst.ElementFactory.make('videoconvert', 'vconv')
        outcaps = Gst.ElementFactory.make('capsfilter',   'voutcaps')
        sink    = Gst.ElementFactory.make('appsink',      'vsink')

        if None in (src, depay, dec, conv, outcaps, sink):
            log.error('[gst-recv] could not instantiate all video recv elements')
            return False

        src.set_property('port', self._info.video_port)
        src.set_property('caps', Gst.Caps.from_string(
            f'application/x-rtp,media=video,clock-rate=90000,'
            f'encoding-name={codec.upper()},payload=96'))

        outcaps.set_property('caps', Gst.Caps.from_string('video/x-raw,format=BGR'))

        sink.set_property('emit-signals', True)
        sink.set_property('max-buffers',  2)
        sink.set_property('drop',         True)
        sink.set_property('sync',         False)
        sink.connect('new-sample', self._pull_frame,
                     self._info.width, self._info.height)

        for el in (src, depay, dec, conv, outcaps, sink):
            p.add(el)

        src.link(depay)

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

        # First-packet probe so the logs confirm data is arriving
        def _pkt_probe(pad, info):
            if not self._first_packet:
                self._first_packet = True
                log.info('[gst-recv] first RTP video packet received')
            return Gst.PadProbeReturn.OK
        src.get_static_pad('src').add_probe(
            Gst.PadProbeType.BUFFER, _pkt_probe)

        bus = p.get_bus()
        bus.set_sync_handler(self._on_bus_sync, None)
        self._pipeline = p
        return True

    def probe(self, timeout_s: float = 1.0) -> bool:
        if not self._pipeline:
            return False
        ret = self._pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: PAUSED failed')
            return False
        bus = self._pipeline.get_bus()
        msg = bus.timed_pop_filtered(int(timeout_s * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: probe error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: probe warning (non-fatal): {warn}')

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: PLAYING failed during probe')
            return False
        msg = bus.timed_pop_filtered(int(1.0 * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        self._pipeline.set_state(Gst.State.NULL)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: probe (PLAYING) error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: probe (PLAYING) warning (non-fatal): {warn}')
        return True

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-recv video] {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-recv video] {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-recv video] EOS')
        return Gst.BusSyncReply.DROP

    def _pull_frame(self, sink, width, height) -> Gst.FlowReturn:
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
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
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] failed to reach PLAYING')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None


class _AudioRecvPipeline:
    """
    Plain-RTP audio receive pipeline (no encryption).

    udpsrc(x-rtp,pt=97) → <rtpdepay> → <decoder>
        → audioconvert → capsfilter(raw) → alsasink / appsink
    """

    def __init__(self, info: GstStreamInfo, dec_name: str,
                 direct_audio: bool, audio_device: str,
                 on_audio: Optional[Callable[[np.ndarray], None]] = None):
        self._info         = info
        self._dec_name     = dec_name
        self._direct_audio = direct_audio
        self._audio_device = audio_device
        self._on_audio     = on_audio
        self._pipeline:    Optional[Gst.Pipeline] = None
        self._first_packet = False

    def build(self) -> bool:
        codec      = self._info.audio_codec
        depay_name = _rtp_audio_depay_name(codec)

        p       = Gst.Pipeline.new('audio-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'asrc')
        depay   = Gst.ElementFactory.make(depay_name,     'adepay')
        dec     = Gst.ElementFactory.make(self._dec_name, 'adec')
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

        if None in (src, depay, dec, conv, outcaps, sink):
            log.error('[gst-recv] could not instantiate all audio recv elements')
            return False

        src.set_property('port', self._info.audio_port)
        # Opus clock-rate is always 48000 per RFC 7587 regardless of output rate
        clock_rate = 48000 if codec == 'opus' else self._info.sample_rate
        src.set_property('caps', Gst.Caps.from_string(
            f'application/x-rtp,media=audio,clock-rate={clock_rate},'
            f'encoding-name={codec.upper()},payload=97'))

        outcaps.set_property('caps', Gst.Caps.from_string(
            f'audio/x-raw,rate={self._info.sample_rate},channels=1'))

        for el in (src, depay, dec, conv, outcaps, sink):
            p.add(el)

        src.link(depay)
        depay.link(dec)
        dec.link(conv)
        conv.link(outcaps)
        outcaps.link(sink)

        def _pkt_probe(pad, info):
            if not self._first_packet:
                self._first_packet = True
                log.info('[gst-recv] first RTP audio packet received')
            return Gst.PadProbeReturn.OK
        src.get_static_pad('src').add_probe(
            Gst.PadProbeType.BUFFER, _pkt_probe)

        bus = p.get_bus()
        bus.set_sync_handler(self._on_bus_sync, None)
        self._pipeline = p
        return True

    def probe(self, timeout_s: float = 1.0) -> bool:
        if not self._pipeline:
            return False
        ret = self._pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: audio PAUSED failed')
            return False
        bus = self._pipeline.get_bus()
        msg = bus.timed_pop_filtered(int(timeout_s * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: audio probe error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: audio probe warning (non-fatal): {warn}')

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: audio PLAYING failed during probe')
            return False
        msg = bus.timed_pop_filtered(int(1.0 * Gst.SECOND),
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        self._pipeline.set_state(Gst.State.NULL)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: audio probe (PLAYING) error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, _ = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: audio probe (PLAYING) warning (non-fatal): {warn}')
        return True

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-recv audio] {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-recv audio] {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-recv audio] EOS')
        return Gst.BusSyncReply.DROP

    def _pull_chunk(self, sink) -> Gst.FlowReturn:
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            chunk = np.frombuffer(mapinfo.data, dtype=np.float32).copy()
            self._on_audio(chunk)
        except Exception as e:
            log.error(f'[gst-recv] audio callback error: {e}')
        finally:
            buf.unmap(mapinfo)
        return Gst.FlowReturn.OK

    def play(self):
        if self._pipeline:
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] failed to reach PLAYING')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None


class GstReceiver:
    """
    Unencrypted GStreamer receiver. Drop-in replacement for the SRTP version.
    """

    def __init__(self,
                 recv_img_callback:       Optional[Callable[[np.ndarray], None]] = None,
                 direct_audio:       bool                                   = False,
                 audio_output_device: str                                   = 'default',
                 recv_audio_callback: Optional[Callable[[np.ndarray], None]] = None):
        self._recv_image_callback = recv_img_callback
        self._direct_audio        = direct_audio
        self._audio_device        = audio_output_device
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
            log.debug(f'[gst-recv] re-announce from {obj.hostname!r}, unchanged — re-acking')
            self._ack(obj)
            return

        log.info(f'[gst-recv] stream info from {obj.hostname!r}: '
                 f'{obj.video_codec} {obj.width}x{obj.height}@{obj.fps} '
                 f'audio={obj.audio_codec}')
        self._stop_pipelines()
        self._info = obj

        if self._recv_image_callback is not None and obj.video_codec:
            for dec_name in _video_decoder_candidates(obj.video_codec):
                log.info(f'[gst-recv] trying video decoder: {dec_name}')
                pipe = _VideoRecvPipeline(obj, dec_name, self._recv_image_callback)
                if not pipe.build():
                    log.warning(f'[gst-recv] {dec_name}: build failed, trying next')
                    continue
                if not pipe.probe():
                    log.warning(f'[gst-recv] {dec_name}: probe failed, trying next')
                    pipe.stop()
                    continue
                log.info(f'[gst-recv] video decoder selected: {dec_name} ({obj.video_codec})')
                self._vpipe = pipe
                pipe.play()
                break
            else:
                log.error('[gst-recv] all video decoders failed — no video will be displayed')
        elif not obj.video_codec:
            log.info('[gst-recv] no video codec in stream info — skipping video pipeline')
        else:
            log.info('[gst-recv] recv_img_callback is None — skipping video pipeline (no screen)')

        if obj.audio_codec:
            self._build_audio_pipeline()
        else:
            log.info('[gst-recv] no audio codec in stream info — skipping audio pipeline')

        self._ack(obj)

    def _build_audio_pipeline(self):
        """(Re)build the audio receive pipeline from self._info, honoring
        the current _direct_audio/_audio_device/_recv_audio_callback
        settings. Shared by on_stream_info() and set_direct_audio() so
        the decoder-selection loop only lives in one place."""
        if self._info is None or not self._info.audio_codec:
            return
        on_audio = (self._recv_audio_callback
                    if not (self._direct_audio and self._recv_audio_callback is not None)
                    else None)
        for dec_name in _audio_decoder_candidates(self._info.audio_codec):
            log.info(f'[gst-recv] trying audio decoder: {dec_name}')
            pipe = _AudioRecvPipeline(
                self._info, dec_name, self._direct_audio, self._audio_device, on_audio=on_audio)
            if not pipe.build():
                log.warning(f'[gst-recv] {dec_name}: build failed, trying next')
                continue
            if not pipe.probe():
                log.warning(f'[gst-recv] {dec_name}: probe failed, trying next')
                pipe.stop()
                continue
            log.info(f'[gst-recv] audio decoder selected: {dec_name} ({self._info.audio_codec})')
            self._apipe = pipe
            pipe.play()
            return
        log.error('[gst-recv] all audio decoders failed — no audio will be played')

    def set_direct_audio(self, direct_audio: bool, audio_device: Optional[str] = None):
        """Switch whether received audio plays straight to a device (e.g.
        a PipeWire virtual device an AI reads via sounddevice, bypassing
        Python entirely for the audio data itself) instead of only going
        through recv_audio_callback, and/or which device it plays to.
        Rebuilds the audio pipeline immediately if a stream is already
        active (self._info set); otherwise just takes effect the next
        time on_stream_info() builds one. The video pipeline is
        untouched either way.
        """
        changed = (direct_audio != self._direct_audio
                  or (audio_device is not None and audio_device != self._audio_device))
        if not changed:
            return
        self._direct_audio = direct_audio
        if audio_device is not None:
            self._audio_device = audio_device
        if self._apipe:
            self._apipe.stop()
            self._apipe = None
        self._build_audio_pipeline()

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