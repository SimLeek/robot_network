"""
robonet/gst_io/receiver_unencrypted.py -- plain RTP receiver (no SRTP).

for testing / local-network use. See todo folder for planned encrypted version.

Pipeline graphs:
    Video: udpsrc(caps=x-rtp,pt=96) -> <rtpdepay> -> [parse] -> <decoder>
               -> videoconvert -> capsfilter(BGR) -> appsink
    Audio: udpsrc(caps=x-rtp,pt=97) -> <rtpdepay> -> <decoder>
               -> audioconvert -> capsfilter(raw) -> alsasink / appsink
"""

from __future__ import annotations
import time
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

    udpsrc(x-rtp,pt=96) -> <rtpdepay> -> [h264/h265parse] -> <decoder>
        -> videoconvert -> capsfilter(BGR) -> appsink
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
            log.warning(f'[gst-recv] {self._dec_name}: probe error: {err} -- {dbg}')
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
            log.warning(f'[gst-recv] {self._dec_name}: probe (PLAYING) error: {err} -- {dbg}')
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

    udpsrc(x-rtp,pt=97) -> <rtpdepay> -> <decoder>
        -> audioconvert -> capsfilter(raw) -> alsasink / appsink
    """

    def __init__(self, info: GstStreamInfo, dec_name: str,
                 direct_audio: bool, audio_device: str,
                 on_audio: Optional[Callable[[np.ndarray], None]] = None,
                 play_locally: bool = False, channels: int = 1):
        self._info         = info
        self._dec_name     = dec_name
        self._direct_audio = direct_audio
        self._audio_device = audio_device
        self._on_audio     = on_audio
        self._play_locally = play_locally
        # Bypasses the wire protocol for now -- GstStreamInfo doesn't
        # carry a channel count yet (mono has been the only option end
        # to end until now), so this has to be told rather than
        # negotiated. Full negotiation is a separate, larger task.
        self._channels      = channels
        self._pipeline:    Optional[Gst.Pipeline] = None
        self._first_packet = False

        # Polling support for non-direct audio visualizer path
        self._running: bool = False
        self._poll_thread: Optional[threading.Thread] = None

    def build(self) -> bool:
        codec      = self._info.audio_codec
        depay_name = _rtp_audio_depay_name(codec)

        p       = Gst.Pipeline.new('audio-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'asrc')
        depay   = Gst.ElementFactory.make(depay_name,     'adepay')
        dec     = Gst.ElementFactory.make(self._dec_name, 'adec')
        conv    = Gst.ElementFactory.make('audioconvert', 'aconv')
        resample = Gst.ElementFactory.make('audioresample', 'aresample')
        outcaps = Gst.ElementFactory.make('capsfilter',   'aoutcaps')

        if self._direct_audio:
            dev = self._audio_device
            if dev and dev not in ('default', 'auto'):
                # An explicit device string (e.g. 'hw:1,0') is honored
                sink = Gst.ElementFactory.make('alsasink', 'asink')
                if sink:
                    sink.set_property('device', dev)
                    sink.set_property('sync', False)
                log.info(f'[gst-recv] audio sink: alsasink device={dev!r}')
            else:
                # Raw ALSA 'default' is frequently a dead end on
                # pipewire/pulse systems: confirmed by direct testing on
                # the actual endpoint, where a test tone on the default
                # device was silent while the pipewire device played
                # fine. autoaudiosink picks pipewiresink / pulsesink /
                # alsasink in whatever order actually works on the box.
                sink = Gst.ElementFactory.make('autoaudiosink', 'asink')
                if sink:
                    try:
                        sink.set_property('sync', False)
                    except TypeError:
                        pass  # property proxying varies by GStreamer version
                log.info('[gst-recv] audio sink: autoaudiosink (auto-routing)')
        else:
            sink = Gst.ElementFactory.make('appsink', 'asink')
            if sink:
                # Critical: Use polling instead of signals to avoid GLib/Python bridge latency
                sink.set_property('emit-signals', False)
                sink.set_property('max-buffers',  4)
                sink.set_property('drop',         False)   # Let leaky queue upstream manage backlog
                sink.set_property('sync',         False)
                sink.set_property('async',        False)
                log.info('[gst-recv] audio sink: appsink (polling mode for visualizer)')

        play_elems = ()
        if (not self._direct_audio) and self._play_locally:
            # Local playback branch, tee'd off AFTER decode/convert/
            # resample but BEFORE the S16LE appsink caps -- the numpy
            # path keeps its fixed S16LE contract while playback
            # negotiates whatever the real hardware wants through its
            # own audioconvert. autoaudiosink for the same reason as
            # the endpoint path: raw ALSA 'default' is a dead end on
            # pipewire/pulse systems. This replaces the sounddevice
            # playback path entirely -- the display loop's vsync
            # blocking starves a realtime Python audio callback into
            # constant clicks/underruns, while GStreamer's own threads
            # are unaffected by it.
            tee      = Gst.ElementFactory.make('tee',           'atee')
            q_app    = Gst.ElementFactory.make('queue',         'aq_app')
            q_app.set_property('max-size-time', 100 * Gst.MSECOND)
            q_app.set_property('leaky', 2)  # Leaky on downstream
            q_play   = Gst.ElementFactory.make('queue',         'aq_play')
            conv2    = Gst.ElementFactory.make('audioconvert',  'aconv2')
            playsink = Gst.ElementFactory.make('autoaudiosink', 'aplaysink')
            if playsink is not None:
                try:
                    playsink.set_property('sync', False)
                except TypeError:
                    pass  # property proxying varies by GStreamer version
            play_elems = (tee, q_app, q_play, conv2, playsink)
            if None in play_elems:
                log.warning('[gst-recv] local playback elements unavailable -- '
                            'continuing without local audio playback')
                play_elems = ()
            else:
                log.info('[gst-recv] local audio playback: autoaudiosink (tee off receive pipeline)')

        if None in (src, depay, dec, conv, resample, outcaps, sink):
            log.error('[gst-recv] could not instantiate all audio recv elements')
            return False

        src.set_property('port', self._info.audio_port)

        # RTP caps for udpsrc
        clock_rate = 48000 if codec == 'opus' else self._info.sample_rate
        src.set_property('caps', Gst.Caps.from_string(
            f'application/x-rtp,media=audio,clock-rate={clock_rate},'
            f'encoding-name={codec.upper()},payload=97'))

        # Output as S16LE (consistent with sender)
        caps = Gst.Caps.from_string(
            f'audio/x-raw,format=S16LE,layout=interleaved,'
            f'rate={self._info.sample_rate},channels={self._channels}'
        )
        outcaps.set_property('caps', caps)
        #outcaps.set_property('caps', Gst.Caps.from_string(
        #    f'audio/x-raw,format=F32LE,layout=interleaved,rate={self._info.sample_rate},channels=1'))

        for el in (src, depay, dec, conv, resample, outcaps, sink) + play_elems:
            p.add(el)

        # Linking
        src.link(depay)
        depay.link(dec)
        dec.link(conv)
        conv.link(resample)
        if play_elems:
            resample.link(tee)
            tee.link(q_app)        # element.link auto-requests a tee src pad
            q_app.link(outcaps)
            tee.link(q_play)
            q_play.link(conv2)
            conv2.link(playsink)
        else:
            resample.link(outcaps)
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
            log.warning(f'[gst-recv] {self._dec_name}: audio probe error: {err} -- {dbg}')
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
            log.warning(f'[gst-recv] {self._dec_name}: audio probe (PLAYING) error: {err} -- {dbg}')
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

    def _poll_loop(self):
        """Dedicated polling thread for appsink to avoid GLib signal latency."""
        sink = self._pipeline.get_by_name('asink') if self._pipeline else None
        if not sink:
            log.error('[gst-recv] polling thread could not find asink')
            return

        while self._running:
            # Short timeout poll (100ms) - non-blocking for the thread
            sample = None
            try:
                # 100ms timeout on try-pull-sample is safe; we sleep only when idle
                sample = sink.emit('try-pull-sample', 100 * Gst.MSECOND)
            except Exception as e:
                log.error(f'[gst-recv] try-pull-sample error: {e}')
                time.sleep(0.01)
                continue
            if sample:
                self._process_sample(sample)
                # Allow processing a few samples in a burst without sleeping
                for _ in range(3):  # small burst handling
                    sample = sink.emit('try-pull-sample', 0)  # non-blocking
                    if sample:
                        self._process_sample(sample)
                    else:
                        break
            else:
                # Yield to other threads / reduce CPU when idle
                time.sleep(0.001)  # 1ms — responsive but very low CPU

    def _process_sample(self, sample):
        """Extract and normalize audio chunk (S16LE -> float32)."""
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            # Caps are S16LE end-to-end (F32LE is not supported by the
            # actual audio hardware on most systems, confirmed by
            # direct testing). Convert to float32 in [-1, 1] here at
            # the boundary so every downstream consumer (display
            # waveform, AI, playback queue) sees one consistent format.
            raw = np.frombuffer(mapinfo.data, dtype=np.int16)
            chunk = raw.astype(np.float32) / 32768.0
            if self._channels > 1:
                # (N, channels), interleaved -- matches the send
                # side's convention. Stays flat 1D for mono (the
                # default), so no existing consumer is affected.
                chunk = chunk.reshape(-1, self._channels)
            if self._on_audio:
                self._on_audio(chunk)
        except Exception as e:
            log.error(f'[gst-recv] audio processing error: {e}')
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
                self._running = True
                # Start polling only for visualizer path (non-direct_audio)
                if not self._direct_audio and self._on_audio is not None:
                    self._poll_thread = threading.Thread(
                        target=self._poll_loop, daemon=True, name='gst-audio-poll')
                    self._poll_thread.start()

    def stop(self):
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=0.5)  # Short join to avoid blocking shutdown
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._poll_thread = None


class GstReceiver:
    """
    Unencrypted GStreamer receiver. Drop-in replacement for the SRTP version.
    """

    def __init__(self,
                 recv_img_callback:       Optional[Callable[[np.ndarray], None]] = None,
                 direct_audio:       bool                                   = False,
                 audio_output_device: str                                   = 'default',
                 recv_audio_callback: Optional[Callable[[np.ndarray], None]] = None,
                 play_locally: bool = False):
        self._recv_image_callback = recv_img_callback
        self._direct_audio        = direct_audio
        self._audio_device        = audio_output_device
        self._recv_audio_callback = recv_audio_callback
        self._play_locally        = play_locally

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
            log.debug(f'[gst-recv] re-announce from {obj.hostname!r}, unchanged -- re-acking')
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
                log.error('[gst-recv] all video decoders failed -- no video will be displayed')
        elif not obj.video_codec:
            log.info('[gst-recv] no video codec in stream info -- skipping video pipeline')
        else:
            log.info('[gst-recv] recv_img_callback is None -- skipping video pipeline (no screen)')

        if obj.audio_codec:
            self._build_audio_pipeline()
        else:
            log.info('[gst-recv] no audio codec in stream info -- skipping audio pipeline')

        self._ack(obj)

    def _build_audio_pipeline(self):
        """(Re)build the audio receive pipeline from self._info."""
        if self._info is None or not self._info.audio_codec:
            return
        on_audio = (self._recv_audio_callback
                    if not (self._direct_audio and self._recv_audio_callback is not None)
                    else None)
        for dec_name in _audio_decoder_candidates(self._info.audio_codec):
            log.info(f'[gst-recv] trying audio decoder: {dec_name}')
            pipe = _AudioRecvPipeline(
                self._info, dec_name, self._direct_audio, self._audio_device, on_audio=on_audio,
                play_locally=self._play_locally)
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
        log.error('[gst-recv] all audio decoders failed -- no audio will be played')

    def set_direct_audio(self, direct_audio: bool, audio_device: Optional[str] = None):
        """Switch whether received audio plays straight to a device, and/or which device it plays to."""
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