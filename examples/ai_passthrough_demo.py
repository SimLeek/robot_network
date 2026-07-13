"""
examples/ai_passthrough_demo.py

Demonstrates AI-driven passthrough control of a connected desktop
endpoint, through the same af_ai neuron/token interface a real AI
would use (not by calling DesktopSubSystem's methods directly) --
mouse moves in a circle, one right-click, one F11 tap, then a few
seconds of a 440Hz sine tone sent as the brain's own outbound audio.

Everything here is chosen to be harmless on a normal desktop: a
right-click just opens a dismissible context menu, F11 toggles
fullscreen (annoying, not destructive), and the sine tone doesn't
touch anything visual at all.

Works with or without a human viewer window:

    python -m examples.ai_passthrough_demo
    python -m examples.ai_passthrough_demo --headless

While the demo runs, desktop.input_source is set to 'ai', so human
mouse/keyboard input is silently dropped (both trying to drive the
same remote cursor at once would just fight each other) -- it's
restored to 'human' automatically when the demo finishes.
"""

from __future__ import annotations

import argparse
import asyncio
import math

from robonet.brain.ai_system import AISubSystem
from robonet.brain.desktop_system import (
    DesktopSubSystem, AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y,
    AI_TOKEN_MOUSE_RIGHT_PRESS, AI_TOKEN_MOUSE_RIGHT_RELEASE,
)
from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.main_system import ServerSystem
from robonet.brain.menu_system import MenuSubSystem
from robonet.brain.radio_system import RadioSubSystem
from robonet.gst_io.streamer_unencrypted import AUDIO_SOURCE_SINE_TEST
from robonet.logging_setup import setup_logging

log = setup_logging()


class AiPassthroughDemo(AISubSystem):
    """Runs the demo sequence once a desktop endpoint connects, then
    hands control back to human input. A minimal concrete AISubSystem
    -- satisfies ServerSystem's `assert displayer or ai` for headless
    runs; a real AI integration would replace this with something that
    actually reads a model's output instead of a fixed script."""

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
        return [self._run()]

    async def _wait_for_desktop_connection(self, poll_interval: float = 0.5) -> DesktopSubSystem:
        while not isinstance(self._root.active_sub, DesktopSubSystem):
            await asyncio.sleep(poll_interval)
        return self._root.active_sub

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

    async def _play_sine_tone(self, seconds: float = 3.0):
        gst_sender = self._root.menu.gst_sender
        original_mic = gst_sender._mic_device
        gst_sender.set_mic_device(AUDIO_SOURCE_SINE_TEST)
        await asyncio.sleep(seconds)
        gst_sender.set_mic_device(original_mic)

    async def _run(self):
        log.info('[ai-demo] waiting for a desktop endpoint to connect...')
        desktop = await self._wait_for_desktop_connection()
        af = desktop.af_ai

        log.info('[ai-demo] connected -- starting passthrough demo')
        desktop.set_input_source('ai')
        try:
            await self._circle_mouse(af)
            await self._right_click(af)
            await asyncio.sleep(0.5)
            await self._tap_f11(desktop)
            await asyncio.sleep(0.5)
            await self._play_sine_tone()
        finally:
            desktop.set_input_source('human')
        log.info('[ai-demo] demo complete -- handed control back to human input')


async def main(headless: bool):
    radio = RadioSubSystem()
    menu = MenuSubSystem()
    demo = AiPassthroughDemo()
    disp = None if headless else DisplaySubSystem()
    serv = ServerSystem(radio, menu, displayer=disp, ai=demo)
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
