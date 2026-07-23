"""
examples/ai_passthrough_demo.py

Demonstrates AI-driven passthrough control of a connected desktop
endpoint.
mouse moves in a circle, one right-click, one F11 tap, then a few
seconds of a 440Hz sine tone sent as the brain's own outbound audio.

Works with or without a human viewer window:

    python -m examples.ai_passthrough_demo
    python -m examples.ai_passthrough_demo --headless

While the demo runs, desktop.input_source is set to 'ai', so human
mouse/keyboard input is silently dropped. It's
restored to 'human' automatically when the demo finishes.
"""

from __future__ import annotations

import argparse
import asyncio
import colorsys
import math

import numpy as np

from robonet.brain.ai_system import AISubSystem
from robonet.brain.desktop_system import (
    DesktopSubSystem, AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y,
    AI_TOKEN_MOUSE_RIGHT_PRESS, AI_TOKEN_MOUSE_RIGHT_RELEASE,
)
from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.main_system import ServerSystem
from robonet.brain.menu_system import MenuSubSystem
from robonet.brain.radio_system import RadioSubSystem
from robonet.bridge.bridge_control import BridgeServer
from robonet.logging_setup import setup_logging

log = setup_logging()


class AiPassthroughDemo(AISubSystem):
    """Runs the demo sequence once a desktop endpoint connects, then
    hands control back to human input. A real AI integration would
    replace this with something that actually reads a model's output
    instead of a fixed script."""

    def __init__(self, radius_frac: float = 0.2, period_s: float = 4.0):
        super().__init__()
        self._root: 'ServerSystem' = None
        self._radius = radius_frac
        self._period = period_s

    def setup(self, root):
        self._root = root

    def start(self):
        pass

    def stop(self):
        pass

    def async_loops(self, sm):
        return [self._run(), self._diagnostic_loop()]

    async def _wait_for_desktop_connection(self, poll_interval: float = 0.5) -> DesktopSubSystem:
        while not isinstance(self._root.active_sub, DesktopSubSystem):
            await asyncio.sleep(poll_interval)
        return self._root.active_sub

    async def _wait_for_media_ready(self, poll_interval: float = 0.5):
        """Connecting only confirms the handshake completed -- the
        actual video/audio pipelines can take several more seconds to
        finish negotiating before real frames start arriving."""
        # note: the actual audio and video np.ndarrays can be found in:
        #   self.in_aud and self.in_img
        while not (self.has_video and self.has_audio):
            await asyncio.sleep(poll_interval)

    async def _circle_mouse(self, af, steps: int = 100):
        for i in range(steps):
            t = (i / steps) * 2 * math.pi
            x = 0.5 + self._radius * math.cos(t)
            y = 0.5 + self._radius * math.sin(t)
            vector = [0.0] * (max(AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y) + 1)
            vector[AI_NEURON_MOUSE_X] = x
            vector[AI_NEURON_MOUSE_Y] = y
            af.on_neuron_outputs(vector)
            await asyncio.sleep(self._period / steps)

    async def _right_click(self, af):
        af.on_token(AI_TOKEN_MOUSE_RIGHT_PRESS)
        await asyncio.sleep(0.1)
        af.on_token(AI_TOKEN_MOUSE_RIGHT_RELEASE)

    async def _tap_f11(self, desktop: DesktopSubSystem):
        desktop.ai_key_press('f11')
        await asyncio.sleep(0.1)
        desktop.ai_key_release('f11')

    async def _play_sine_tone(self, seconds: float = 5.0, freq_hz: float = 440.0,
                               sample_rate: int = 48000):
        gst_sender = self._root.menu.gst_sender
        t = np.arange(0, seconds, 1.0 / sample_rate)
        tone = (0.5 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
        gst_sender.play_array(tone, sample_rate)
        # A freshly built audio pipeline needs a moment before real
        # samples flow -- settle before returning, so callers waiting
        # on this coroutine know the tone has actually had time to play.
        await asyncio.sleep(2.0 + seconds)

    async def _stream_audio_demo(self, seconds: float = 5.0, freq_hz: float = 880.0,
                                 chunk_dur: float = 0.2, sample_rate: int = 48000):
        """Demonstrates GstSender.start_audio_stream()/push()/end() --
        an octave above _play_sine_tone's 440Hz so the two are
        distinguishable by ear."""
        gst_sender = self._root.menu.gst_sender
        handle = gst_sender.start_audio_stream(sample_rate=sample_rate)
        if handle is None:
            log.error('[ai-demo] could not start the audio stream')
            return
        t0 = 0.0
        n_chunks = int(seconds / chunk_dur)
        for _ in range(n_chunks):
            t = t0 + np.arange(0, chunk_dur, 1.0 / sample_rate)
            chunk = (0.5 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
            handle.push(chunk)
            t0 += chunk_dur
            await asyncio.sleep(chunk_dur)
        handle.end()
        await asyncio.sleep(2.0)  # same settle reasoning as _play_sine_tone

    async def _read_selection_demo(self, desktop: 'DesktopSubSystem'):
        log.info('[ai-demo] requesting the endpoint\'s highlighted text...')
        desktop.ai_read_selection()
        await asyncio.sleep(2.0)

    async def _diagnostic_loop(self, interval_s: float = 1.0, sample_rate: int = 48000):
        while True:
            await asyncio.sleep(interval_s)
            img = self.in_img
            if img is not None and img.ndim == 3 and img.shape[0] and img.shape[1]:
                cy, cx = img.shape[0] // 2, img.shape[1] // 2
                r, g, b = (float(v) / 255.0 for v in img[cy, cx, :3])
                hue_deg = colorsys.rgb_to_hsv(r, g, b)[0] * 360.0
                log.info(f'[ai-demo] center pixel hue: {hue_deg:.1f} deg')
            aud = self.in_aud
            if aud is not None and len(aud) >= 8:
                spectrum = np.abs(np.fft.rfft(aud, axis=0))
                freqs = np.fft.rfftfreq(aud.shape[0], d=1.0 / sample_rate)
                peak_indices = np.argmax(spectrum, axis=0)
                peak_hz = freqs[peak_indices]
                log.info(f'[ai-demo] peak audio frequencies: {peak_hz}')

    async def _run(self):
        log.info('[ai-demo] waiting for a desktop endpoint to connect...')
        desktop = await self._wait_for_desktop_connection()
        af = desktop.af_ai

        log.info('[ai-demo] connected -- waiting for video/audio to actually start flowing...')
        await self._wait_for_media_ready()

        log.info('[ai-demo] video and audio confirmed flowing -- starting passthrough demo')
        desktop.set_input_source('ai')
        try:
            await self._circle_mouse(af)
            await self._right_click(af)
            await asyncio.sleep(0.5)
            await self._tap_f11(desktop)
            await asyncio.sleep(0.5)
            await self._play_sine_tone()
            await self._stream_audio_demo()
            await self._read_selection_demo(desktop)
        finally:
            desktop.set_input_source('human')
        log.info('[ai-demo] demo complete -- handed control back to human input')


async def main(headless: bool):
    radio = RadioSubSystem()
    menu = MenuSubSystem()
    demo = AiPassthroughDemo()
    disp = None if headless else DisplaySubSystem()
    bridge = BridgeServer()  # optional: lets a separate real AI process (e.g. examples/shmem_bridge_ai.py) also attach
    serv = ServerSystem(radio, menu, displayer=disp, ai=demo, bridge=bridge)
    serv.start()

    loops = serv.async_loops()
    await asyncio.gather(*loops)

    serv.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true',
                        help='run with no viewer window -- AI passthrough works with or without one')
    args = parser.parse_args()
    asyncio.run(main(args.headless))
