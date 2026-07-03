"""
robonet/brain/ai_audio.py

The audio half of "a few functions is all a user needs to see to get
their system working with this one." Two functions:

    setup_ai_audio_input(sm, callback)   -- AI hears the connected endpoint
    setup_ai_audio_output(sm, callback)  -- AI speaks through the endpoint

Both create a PipeWire virtual device pair (via the `pw-loopback` CLI
tool -- part of a normal pipewire install) and redirect the brain's
existing GstSender/GstReceiver to read from or write to it, instead of a
real microphone or the human-facing display. The AI side only ever has
to write a plain Python function; robonet owns the sounddevice stream
and PipeWire device lifecycle.

Why this exists rather than just wiring MenuSubSystem.on_audio straight
to an AI callback: routing through a real virtual audio device gets you
an exact, AI-chosen blocksize/sample_rate independent of whatever
GStreamer's own internal buffering happens to produce, and it means the
AI-side code is standard sounddevice-shaped -- nothing here requires the
caller to import GStreamer or robonet's network internals, just numpy.

The AI is always assumed to run on the brain side (this module lives in
robonet/brain/, not robonet/endpoint/) -- the one case where that's the
same physical machine as the endpoint is LOCALHOST mode, which needs no
special handling here since it's still brain-side code either way.

Neuron count/Hz for whatever blocksize/sample_rate you choose:
see robonet.audio_io.audio_neuron_spec and blocksize_for_max_hz.

Environment note this could not be verified against a live PipeWire
server in the environment this was written in: sounddevice (PortAudio)
opening a pw-loopback node by its node.name string, and GStreamer's
alsasrc/alsasink addressing that same node by name, both depend on the
PipeWire ALSA/JACK compatibility layers being set up normally (which
they are by default on a current Arch install with pipewire +
pipewire-alsa + pipewire-jack). If device name matching doesn't resolve
on a given machine, `python -m sounddevice` lists what PortAudio
actually sees the node as, and that's the string to pass as device_name
here instead.
"""

from __future__ import annotations

import subprocess
import time
import typing
from typing import Callable, Optional

import numpy as np

_SOUNDDEVICE_IMPORT_ERROR = None
try:
    import sounddevice
except Exception as _e:
    sounddevice = None
    _SOUNDDEVICE_IMPORT_ERROR = _e

from robonet.audio_io import AudioNeuronSpec, audio_neuron_spec
from robonet.logging_setup import setup_logging

log = setup_logging()

if typing.TYPE_CHECKING:
    from robonet.brain.main_system import ServerSystem

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_BLOCKSIZE = 480  # 100Hz at 48kHz -- a reasonable default, not a requirement


class AudioIOError(Exception):
    """Raised when pw-loopback or the sounddevice stream couldn't be
    started. Callers can also just check for a None return instead of
    catching this -- both setup functions log the reason either way."""
    pass


class _PwLoopback:
    """Wraps a `pw-loopback` subprocess creating one virtual PipeWire
    device pair. Whatever gets played into the playback_node comes back
    out the capture_node -- standard loopback pair semantics, just
    PipeWire-native instead of the ALSA snd-aloop kernel module
    desktop_capture.py uses for the same kind of thing on the endpoint
    side. Which side is "AI" vs "GStreamer" depends on the direction
    (see setup_ai_audio_input/output below); this class doesn't care.
    """

    def __init__(self, name: str):
        self.name = name
        self.playback_node = f'{name}_playback'
        self.capture_node = f'{name}_capture'
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        cmd = [
            'pw-loopback',
            '--capture-props', f'node.name={self.capture_node}',
            '--playback-props', f'node.name={self.playback_node}',
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError:
            log.error("[ai-audio] 'pw-loopback' not found on PATH -- install pipewire "
                     "(most distros' pipewire package includes pw-loopback; some split it "
                     "into a pipewire-utils/pipewire-bin package) and try again")
            return False
        time.sleep(0.3)  # give the nodes a moment to register before anything opens them
        if self._proc.poll() is not None:
            stderr = self._proc.stderr.read().decode(errors='replace') if self._proc.stderr else ''
            log.error(f'[ai-audio] pw-loopback exited immediately: {stderr}')
            return False
        return True

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


class AudioIOHandle:
    """Returned by setup_ai_audio_input/output. Call .stop() to tear
    down both the sounddevice stream and the PipeWire virtual device."""

    def __init__(self, loopback: _PwLoopback, stream, neuron_spec: AudioNeuronSpec):
        self._loopback = loopback
        self._stream = stream
        self.neuron_spec = neuron_spec

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception('[ai-audio] error stopping sounddevice stream')
        self._loopback.stop()


def _check_sounddevice():
    if sounddevice is None:
        raise AudioIOError(
            f'sounddevice could not be imported: {_SOUNDDEVICE_IMPORT_ERROR}. '
            'Install it (pip install sounddevice) and make sure libportaudio2 '
            '(or your distro\'s equivalent) is installed.'
        )


def setup_ai_audio_input(sm: 'ServerSystem', callback: Callable[[np.ndarray], None],
                         sample_rate: int = DEFAULT_SAMPLE_RATE,
                         blocksize: int = DEFAULT_BLOCKSIZE,
                         device_name: str = 'robonet_ai_in') -> Optional[AudioIOHandle]:
    """Lets an AI hear the connected endpoint's audio.

    Redirects the brain's existing GstReceiver audio path into a
    PipeWire virtual device (instead of only handing chunks to the human
    display via on_audio) and opens a sounddevice.InputStream against it
    that calls callback(audio_block) once per block -- mono float32,
    length == blocksize. One neuron per sample, sample_rate/blocksize
    Hz; see robonet.audio_io.audio_neuron_spec.

    Returns None (after logging why) if pw-loopback or the sounddevice
    stream couldn't be started. The callback itself is never allowed to
    crash the audio thread -- exceptions are caught and logged.
    """
    _check_sounddevice()
    loopback = _PwLoopback(device_name)
    if not loopback.start():
        return None

    sm.menu.gst_receiver.set_direct_audio(True, loopback.playback_node)

    def _on_block(indata, frames, time_info, status):
        if status:
            log.warning(f'[ai-audio] input stream status: {status}')
        try:
            callback(indata[:, 0].copy())
        except Exception:
            log.exception('[ai-audio] input callback raised')

    try:
        stream = sounddevice.InputStream(
            device=loopback.capture_node, channels=1, samplerate=sample_rate,
            blocksize=blocksize, dtype='float32', callback=_on_block)
        stream.start()
    except Exception as e:
        log.error(f'[ai-audio] could not open input stream on {loopback.capture_node!r}: {e}')
        loopback.stop()
        return None

    log.info(f'[ai-audio] input ready: {loopback.capture_node!r} -> callback, '
            f'{blocksize} samples @ {sample_rate}/{blocksize}={sample_rate/blocksize:.1f}Hz')
    return AudioIOHandle(loopback, stream, audio_neuron_spec(sample_rate, blocksize))


def setup_ai_audio_output(sm: 'ServerSystem', callback: Callable[[int], np.ndarray],
                          sample_rate: int = DEFAULT_SAMPLE_RATE,
                          blocksize: int = DEFAULT_BLOCKSIZE,
                          device_name: str = 'robonet_ai_out') -> Optional[AudioIOHandle]:
    """Lets an AI speak through the connected endpoint.

    Opens a sounddevice.OutputStream against a PipeWire virtual device
    that calls callback(num_frames) -> np.ndarray once per block to pull
    audio from the AI (mono float32, ideally length == num_frames --
    shorter is zero-padded, longer is truncated), and redirects the
    brain's existing GstSender's mic source to read from that device
    instead of a real microphone. One neuron per sample,
    sample_rate/blocksize Hz; see robonet.audio_io.audio_neuron_spec.

    Returns None (after logging why) if pw-loopback or the sounddevice
    stream couldn't be started. The callback itself is never allowed to
    crash the audio thread -- exceptions are caught, logged, and produce
    silence for that block instead.
    """
    _check_sounddevice()
    loopback = _PwLoopback(device_name)
    if not loopback.start():
        return None

    sm.menu.gst_sender.set_mic_device(loopback.capture_node)

    def _on_block(outdata, frames, time_info, status):
        if status:
            log.warning(f'[ai-audio] output stream status: {status}')
        try:
            data = np.asarray(callback(frames), dtype='float32').reshape(-1)
            if len(data) < frames:
                data = np.pad(data, (0, frames - len(data)))
            elif len(data) > frames:
                data = data[:frames]
            outdata[:, 0] = data
        except Exception:
            log.exception('[ai-audio] output callback raised')
            outdata.fill(0)

    try:
        stream = sounddevice.OutputStream(
            device=loopback.playback_node, channels=1, samplerate=sample_rate,
            blocksize=blocksize, dtype='float32', callback=_on_block)
        stream.start()
    except Exception as e:
        log.error(f'[ai-audio] could not open output stream on {loopback.playback_node!r}: {e}')
        loopback.stop()
        return None

    log.info(f'[ai-audio] output ready: callback -> {loopback.playback_node!r}, '
            f'{blocksize} samples @ {sample_rate}/{blocksize}={sample_rate/blocksize:.1f}Hz')
    return AudioIOHandle(loopback, stream, audio_neuron_spec(sample_rate, blocksize))
