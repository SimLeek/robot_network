"""
robonet/brain/ai_audio.py

Two main functions:

    setup_ai_audio_input(sm, callback)   -- AI hears the connected endpoint
    setup_ai_audio_output(sm, callback)  -- AI speaks through the endpoint

Neuron count/Hz depends on whatever blocksize/sample_rate you choose:
see robonet.audio_io.audio_neuron_spec and blocksize_for_max_hz.
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
    """Raised when pw-loopback or the sounddevice stream couldn't be started."""
    pass


class _PwLoopback:
    """Wraps a `pw-loopback` subprocess creating one virtual PipeWire device pair."""

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

    Returns None (after logging why) if pw-loopback or the sounddevice
    stream couldn't be started. AI being able to start at all is a
    top priority, so exceptions are caught and logged instead.
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

    Returns None (after logging why) if pw-loopback or the sounddevice
    stream couldn't be started. AI being able to start at all is a
    top priority, so exceptions are caught and logged instead.
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
