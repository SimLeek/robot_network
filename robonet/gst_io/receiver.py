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


def _video_decoder_candidates(codec: str) -> list[str]:
    """
    Return all available video decoders as an ordered list for the given codec.

    Registry-present but broken decoders (e.g. hardware decoders with driver
    issues) are weeded out at probe time, not here.
    """
    ordered = {
        'h264': ['nvh264dec',  'vaapih264dec',  'v4l2h264dec',  'avdec_h264'],
        'h265': ['nvh265dec',  'vaapih265dec',  'v4l2h265dec',  'avdec_h265'],
        'vp8':  ['nvvp8dec',   'vaapivp8dec',   'v4l2vp8dec',   'vp8dec',  'avdec_vp8'],
        'vp9':  ['nvvp9dec',   'vaapivp9dec',   'v4l2vp9dec',   'vp9dec',  'avdec_vp9'],
    }.get(codec, [f'avdec_{codec}'])
    found = [name for name in ordered if _has(name)]
    if not found:
        log.warning(f'[gst-recv] no video decoder found for codec {codec!r} in GStreamer registry')
    else:
        log.info(f'[gst-recv] video decoder candidates for {codec}: {found}')
    return found


def _audio_decoder_candidates(codec: str) -> list[str]:
    """
    Return all available audio decoders as an ordered list for the given codec.
    """
    ordered = {
        'opus': ['opusdec'],
        'aac':  ['avdec_aac', 'faad'],
        'mp3':  ['mpg123audiodec', 'avdec_mp3'],
        'flac': ['flacdec'],
    }.get(codec, [f'avdec_{codec}'])
    found = [name for name in ordered if _has(name)]
    if not found:
        log.warning(f'[gst-recv] no audio decoder found for codec {codec!r} in GStreamer registry')
    else:
        log.info(f'[gst-recv] audio decoder candidates for {codec}: {found}')
    return found


def _rtp_depay_name(codec: str) -> str:
    return {'h264':'rtph264depay','h265':'rtph265depay',
            'vp8':'rtpvp8depay','vp9':'rtpvp9depay'}[codec]


def _rtp_audio_depay_name(codec: str) -> str:
    return {'opus':'rtpopusdepay','aac':'rtpmp4adepay',
            'mp3':'rtpmpadepay','flac':'rtpgstdepay'}.get(codec, 'rtpgstdepay')


def _srtp_key_from_psk(psk: bytes) -> bytes:
    """
    Derive a 30-byte SRTP master-key+salt from the robonet PSK using BLAKE2b.
    """
    return hashlib.blake2b(psk, digest_size=30).digest()


#def _srtp_caps(pt: int, key: bytes) -> Gst.Caps:
#    """
#    Build the GstCaps required by srtpdec.
#    """
#    return Gst.Caps.from_string(
#        f'application/x-srtp, payload=(int){pt}, '
#        f'srtp-key=(buffer){key.hex()}, '
#        f'srtp-cipher=(string)aes-128-icm, '
#        f'srtp-auth=(string)hmac-sha1-80, '
#        f'srtcp-cipher=(string)aes-128-icm, '
#        f'srtcp-auth=(string)hmac-sha1-80, '
#        f'roc=(uint)0')


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

    def __init__(self, info: GstStreamInfo, dec_name: str, srtp_key: bytes,
                 on_frame: Callable[[np.ndarray], None]):
        self._info      = info
        self._dec_name  = dec_name
        self._srtp_key  = srtp_key
        self._on_frame  = on_frame
        self._received_packet  = False
        self._received_encrypted = False
        self._received_decrypted = False
        self._pipeline: Optional[Gst.Pipeline] = None

    def build(self) -> bool:
        codec = self._info.video_codec
        depay_name = _rtp_depay_name(codec)
        p       = Gst.Pipeline.new('video-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'vsrc')
        capsflt = Gst.ElementFactory.make('capsfilter',   'vcaps')
        srtpdec = Gst.ElementFactory.make('srtpdec',      'vsrtpdec')
        depay   = Gst.ElementFactory.make(depay_name,     'vdepay')
        dec     = Gst.ElementFactory.make(self._dec_name, 'vdec')
        conv    = Gst.ElementFactory.make('videoconvert', 'vconv')
        outcaps = Gst.ElementFactory.make('capsfilter',   'voutcaps')
        sink    = Gst.ElementFactory.make('appsink',      'vsink')

        if None in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            log.error('[gst-recv] could not instantiate all video recv elements — '
                      'check gst-plugins-bad and gst-plugin-srtp are installed')
            return False

        src.set_property('port', self._info.video_port)
        capsflt.set_property('caps', Gst.Caps.from_string('application/x-srtp, payload=(int)96, ssrc=(uint)0, roc=(uint)0'))

        def _link_video_src_to_depay(element, src_pad):
            """Find depay's unlinked sink and connect src_pad to it."""
            sink_pad = depay.get_static_pad('sink')
            if not sink_pad:
                log.error('[gst-recv] video depay has no static sink pad')
                return
            if sink_pad.is_linked():
                log.info('[gst-recv] video depay static sink pad is linked')
                return
            result = src_pad.link(sink_pad)
            if result == Gst.PadLinkReturn.OK:
                log.info(f'[gst-recv] video srtpdec → depay linked (pad={src_pad.get_name()})')
            else:
                log.error(f'[gst-recv] video srtpdec → depay link failed: {result} (pad={src_pad.get_name()})')

        def _on_video_request_key(element, ssrc):
            log.info(f'[gst-recv] Supplying video SRTP key for SSRC {ssrc}')
            caps = Gst.Caps.from_string(
                f'application/x-srtp, ssrc=(uint){ssrc}, '
                f'srtp-key=(buffer){self._srtp_key.hex()}, '
                f'srtp-cipher=(string)aes-128-icm, srtp-auth=(string)hmac-sha1-80, '
                f'srtcp-cipher=(string)aes-128-icm, srtcp-auth=(string)hmac-sha1-80, roc=(uint)0'
            )
            # GStreamer source creates the src pad before firing request-key,
            # so it should exist by now.  We search by iteration rather than
            # by name because the naming convention differs across versions.
            sink_pad = depay.get_static_pad('sink')
            if sink_pad and not sink_pad.is_linked():
                it = element.iterate_src_pads()
                while True:
                    res, pad = it.next()
                    if res != Gst.IteratorResult.OK:
                        break
                    log.info(f"[gst-recv] video sink pad name: {pad.get_name()}")
                    if 'rtp_src' in pad.get_name():
                        _link_video_src_to_depay(element, pad)
                        break
                if not sink_pad.is_linked():
                    log.warning(f'[gst-recv] video: no rtp_src pad found during request-key for SSRC {ssrc}')
            return caps

        srtpdec.connect('request-key', _on_video_request_key)

        # pad-added fires if the pad is created after request-key returns
        # (timing varies by GStreamer version).  One of the two handlers will win.
        def _on_video_pad_added(element, pad):
            if 'rtp_src' in pad.get_name():
                _link_video_src_to_depay(element, pad)
        srtpdec.connect('pad-added', _on_video_pad_added)
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

        # SRTP key hash (for verification)
        key_hash = hashlib.sha256(self._srtp_key).hexdigest()[:16]
        log.info(f'[gst-recv] SRTP KEY HASH video: {key_hash} (dec_name={self._dec_name})')

        # Probe 1: encrypted packets entering srtpdec
        def _srtpdec_sink_probe(pad, info):
            if not self._received_encrypted:
                log.info(f'[gst-recv] ENCRYPTED buffer arrived at srtpdec sink (video)')
                self._received_encrypted = True
            return Gst.PadProbeReturn.OK
        # Probing 'capsflt.src' because 'srtpdec.sink' is a Request Pad and doesn't exist yet.
        # Data leaving capsflt is exactly what enters srtpdec.
        capsflt.get_static_pad('src').add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
            _srtpdec_sink_probe)

        # Probe 2: decrypted RTP leaving srtpdec
        def _post_srtp_probe(pad, info):
            if not self._received_decrypted:
                log.info(f'[gst-recv] DECRYPTED RTP buffer reached depay/decoder (video)')
                self._received_decrypted = True
            return Gst.PadProbeReturn.OK

        def _add_post_probe(element, pad, _):
            # Only attach to the newly created source pad of srtpdec
            pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, _post_srtp_probe)
        srtpdec.connect('pad-added', _add_post_probe, None)

        # Existing pre-srtpdec probe (kept)
        def _packet_probe(pad, info):
            if not self._received_packet:
                self._received_packet = True
                log.info(f'[gst-recv] FIRST SRTP packet received → data is flowing to {self._dec_name} pipeline')
            return Gst.PadProbeReturn.OK
        capsflt.get_static_pad('src').add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
            _packet_probe)

        bus = p.get_bus()
        bus.set_sync_handler(self._on_bus_sync, None)

        self._pipeline = p
        return True

    def probe(self, timeout_s: float = 1.0) -> bool:
        """
        Set the pipeline to PAUSED and listen for bus errors for up to
        timeout_s seconds.

        Returns True if no error was observed, False otherwise.
        Call stop() before discarding a pipeline that failed probe().
        """
        if not self._pipeline:
            return False

        # 1. Quick PAUSED check (catches element creation / caps issues)
        ret = self._pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: PAUSED state change failed immediately')
            return False

        bus = self._pipeline.get_bus()
        deadline = int(timeout_s * Gst.SECOND)
        msg = bus.timed_pop_filtered(deadline,
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: probe (PAUSED) error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, dbg = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: probe (PAUSED) warning (non-fatal): {warn}')

        # 2. Short PLAYING test — this is what catches "Failed to process frame"
        #    style errors that only happen once data actually flows.
        log.debug(f'[gst-recv] {self._dec_name}: starting 1 s PLAYING probe test')
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: PLAYING state change failed during probe test')
            return False

        test_deadline = int(1.0 * Gst.SECOND)
        msg = bus.timed_pop_filtered(test_deadline,
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)

        # Stop the test run (we will call play() again if this probe succeeds)
        self._pipeline.set_state(Gst.State.NULL)

        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: probe test (PLAYING) error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, dbg = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: probe test warning (non-fatal): {warn}')

        log.debug(f'[gst-recv] {self._dec_name}: probe test passed')
        return True

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-recv video] pipeline error: {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-recv video] pipeline warning: {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-recv video] EOS received')
        return Gst.BusSyncReply.DROP

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
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] Failed to transition to PLAYING state!')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

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

    def __init__(self, info: GstStreamInfo, dec_name: str, srtp_key: bytes,
                 direct_audio: bool, audio_device: str,
                 on_audio: Optional[Callable[[np.ndarray], None]] = None):
        self._info         = info
        self._dec_name     = dec_name
        self._srtp_key     = srtp_key
        self._direct_audio = direct_audio
        self._audio_device = audio_device
        self._pipeline:    Optional[Gst.Pipeline] = None
        self._on_audio = on_audio
        self._received_packet = False
        self._received_encrypted = False
        self._received_decrypted = False

    def build(self) -> bool:
        codec      = self._info.audio_codec
        depay_name = _rtp_audio_depay_name(codec)
        p       = Gst.Pipeline.new('audio-recv')
        src     = Gst.ElementFactory.make('udpsrc',       'asrc')
        capsflt = Gst.ElementFactory.make('capsfilter',   'acaps')
        srtpdec = Gst.ElementFactory.make('srtpdec',      'asrtpdec')
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

        if None in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            log.error('[gst-recv] could not instantiate all audio recv elements — '
                      'check gst-plugins-bad and gst-plugin-srtp are installed')
            return False

        src.set_property('port', self._info.audio_port)
        capsflt.set_property('caps', Gst.Caps.from_string('application/x-srtp, payload=(int)97, ssrc=(uint)0, roc=(uint)0'))

        def _link_audio_src_to_depay(element, src_pad):
            sink_pad = depay.get_static_pad('sink')
            if not sink_pad:
                log.error('[gst-recv] audio depay has no static sink pad')
                return
            if sink_pad.is_linked():
                log.info('[gst-recv] audio depay static sink pad is linked')
                return
            result = src_pad.link(sink_pad)
            if result == Gst.PadLinkReturn.OK:
                log.info(f'[gst-recv] audio srtpdec → depay linked (pad={src_pad.get_name()})')
            else:
                log.error(f'[gst-recv] audio srtpdec → depay link failed: {result} (pad={src_pad.get_name()})')

        def _on_audio_request_key(element, ssrc):
            log.info(f'[gst-recv] Supplying audio SRTP key for SSRC {ssrc}')
            caps = Gst.Caps.from_string(
                f'application/x-srtp, ssrc=(uint){ssrc}, '
                f'srtp-key=(buffer){self._srtp_key.hex()}, '
                f'srtp-cipher=(string)aes-128-icm, srtp-auth=(string)hmac-sha1-80, '
                f'srtcp-cipher=(string)aes-128-icm, srtcp-auth=(string)hmac-sha1-80, roc=(uint)0'
            )
            sink_pad = depay.get_static_pad('sink')
            if sink_pad and not sink_pad.is_linked():
                it = element.iterate_src_pads()
                while True:
                    res, pad = it.next()
                    if res != Gst.IteratorResult.OK:
                        break
                    log.info(f"[gst-recv] audio sink pad name: {pad.get_name()}")
                    if 'rtp_src' in pad.get_name():
                        _link_audio_src_to_depay(element, pad)
                        break
                if not sink_pad.is_linked():
                    log.warning(f'[gst-recv] audio: no rtp_src pad found during request-key for SSRC {ssrc}')
            return caps

        srtpdec.connect('request-key', _on_audio_request_key)

        def _on_audio_pad_added(element, pad):
            if 'rtp_src' in pad.get_name():
                _link_audio_src_to_depay(element, pad)
        srtpdec.connect('pad-added', _on_audio_pad_added)
        outcaps.set_property('caps', Gst.Caps.from_string(
            f'audio/x-raw,rate={self._info.sample_rate},channels=1'))

        for el in (src, capsflt, srtpdec, depay, dec, conv, outcaps, sink):
            p.add(el)
        src.link(capsflt)
        capsflt.link(srtpdec)

        depay.link(dec)
        dec.link(conv)
        conv.link(outcaps)
        outcaps.link(sink)

        # SRTP key hash
        key_hash = hashlib.sha256(self._srtp_key).hexdigest()[:16]
        log.info(f'[gst-recv] SRTP KEY HASH audio: {key_hash} (dec_name={self._dec_name})')

        def _srtpdec_sink_probe(pad, info):
            if not self._received_encrypted:
                log.info(f'[gst-recv] ENCRYPTED buffer arrived at srtpdec sink (audio)')
                self._received_encrypted = True
            return Gst.PadProbeReturn.OK
        # Probing 'capsflt.src' because 'srtpdec.sink' is a Request Pad and doesn't exist yet.
        # Data leaving capsflt is exactly what enters srtpdec.
        capsflt.get_static_pad('src').add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
            _srtpdec_sink_probe)

        def _post_srtp_probe(pad, info):
            if not self._received_decrypted:
                log.info(f'[gst-recv] DECRYPTED RTP buffer reached depay/decoder (audio)')
                self._received_decrypted = True
            return Gst.PadProbeReturn.OK

        def _add_post_probe(element, pad, _):
            # Only attach to the newly created source pad of srtpdec
            pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, _post_srtp_probe)
        srtpdec.connect('pad-added', _add_post_probe, None)

        def _packet_probe(pad, info):
            if not self._received_packet:
                self._received_packet = True
                log.info(f'[gst-recv] FIRST SRTP packet received → data is flowing to {self._dec_name} pipeline (audio)')
            return Gst.PadProbeReturn.OK
        capsflt.get_static_pad('src').add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
            _packet_probe)

        bus = p.get_bus()
        bus.set_sync_handler(self._on_bus_sync, None)

        self._pipeline = p
        return True

    def probe(self, timeout_s: float = 1.0) -> bool:
        """
        Set the pipeline to PAUSED and listen for immediate errors,
        then run a short PLAYING test (1 second) to catch runtime failures
        that only appear once the first buffer reaches the decoder.
        """
        if not self._pipeline:
            return False

        # 1. Quick PAUSED check
        ret = self._pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: audio PAUSED state change failed')
            return False

        bus = self._pipeline.get_bus()
        deadline = int(timeout_s * Gst.SECOND)
        msg = bus.timed_pop_filtered(deadline,
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: audio probe (PAUSED) error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, dbg = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: audio probe (PAUSED) warning (non-fatal): {warn}')

        # 2. Short PLAYING test
        log.debug(f'[gst-recv] {self._dec_name}: starting 1 s PLAYING probe test (audio)')
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[gst-recv] {self._dec_name}: audio PLAYING state change failed during probe test')
            return False

        test_deadline = int(1.0 * Gst.SECOND)
        msg = bus.timed_pop_filtered(test_deadline,
                                     Gst.MessageType.ERROR | Gst.MessageType.WARNING)

        # Stop the test run
        self._pipeline.set_state(Gst.State.NULL)

        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.warning(f'[gst-recv] {self._dec_name}: audio probe test (PLAYING) error: {err} — {dbg}')
            return False
        if msg and msg.type == Gst.MessageType.WARNING:
            warn, dbg = msg.parse_warning()
            log.info(f'[gst-recv] {self._dec_name}: audio probe test warning (non-fatal): {warn}')

        log.debug(f'[gst-recv] {self._dec_name}: audio probe test passed')
        return True

    @staticmethod
    def _on_bus_sync(bus, message, _data):
        if message.type == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            log.error(f'[gst-recv audio] pipeline error: {err} | {dbg}')
        elif message.type == Gst.MessageType.WARNING:
            warn, dbg = message.parse_warning()
            log.warning(f'[gst-recv audio] pipeline warning: {warn} | {dbg}')
        elif message.type == Gst.MessageType.EOS:
            log.warning('[gst-recv audio] EOS received')
        return Gst.BusSyncReply.DROP

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
            ret = self._pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                log.error(f'[{self.__class__.__name__}] Failed to transition to PLAYING state!')
            else:
                log.info(f'[{self.__class__.__name__}] successfully transitioned to PLAYING.')

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

        # SRTP key hash (compare this on both sides)
        key_hash = hashlib.sha256(self._srtp_key).hexdigest()[:16]
        log.info(f'[gst-recv] SRTP KEY HASH (receiver init): {key_hash}')

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
            candidates = _video_decoder_candidates(obj.video_codec)
            for dec_name in candidates:
                log.info(f'[gst-recv] trying video decoder: {dec_name}')
                pipe = _VideoRecvPipeline(
                    obj, dec_name, self._srtp_key, self._recv_image_callback)
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
                log.error('[gst-recv] all video decoders failed probe — no video will be displayed')
        elif not obj.video_codec:
            log.info('[gst-recv] no video codec in stream info — skipping video pipeline')
        else:
            log.info('[gst-recv] set_last_img is None — skipping video pipeline (no screen)')

        if obj.audio_codec:
            candidates = _audio_decoder_candidates(obj.audio_codec)
            for dec_name in candidates:
                log.info(f'[gst-recv] trying audio decoder: {dec_name}')
                on_audio = (
                    self._recv_audio_callback
                    if not (self._direct_audio and self._recv_audio_callback is not None)
                    else None
                )
                pipe = _AudioRecvPipeline(
                    obj, dec_name, self._srtp_key,
                    self._direct_audio, self._audio_device, on_audio=on_audio)
                if not pipe.build():
                    log.warning(f'[gst-recv] {dec_name}: build failed, trying next')
                    continue
                if not pipe.probe():
                    log.warning(f'[gst-recv] {dec_name}: probe failed, trying next')
                    pipe.stop()
                    continue
                log.info(f'[gst-recv] audio decoder selected: {dec_name} ({obj.audio_codec})')
                self._apipe = pipe
                pipe.play()
                break
            else:
                log.error('[gst-recv] all audio decoders failed probe — no audio will be played')
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