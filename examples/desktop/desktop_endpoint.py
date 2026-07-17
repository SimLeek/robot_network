"""
examples/desktop/desktop_endpoint.py -- A remote desktop endpoint

Start once and leave running in the background
(e.g. as a systemd --user service);

If connecting over a direct wired (ethernet) link,
also run this once on the endpoint side to get a static IP:
    python -m examples.setup_eth_client
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np

from robonet.endpoint.base import RobotNode
from robonet.endpoint.desktop_capture import DesktopCaptureError
from robonet.endpoint.desktop_hardware import DesktopHw
from robonet.endpoint.radio_system import RobotRadio

log = logging.getLogger(__name__)


async def audio_stream_demo(hw: DesktopHw, seconds: float = 5.0, freq_hz: float = 440.0,
                            chunk_dur: float = 0.2, sample_rate: int = 48000,
                            poll_interval: float = 1.0):
    """Demonstrates GstSender.start_audio_stream()/push()/end() from
    the endpoint side: streams a test tone to the connected brain in
    chunks, once, so the streaming API can be verified on real
    hardware rather than only in the sandboxed loopback tests. Waits
    for the first brain connection, plays through once, then returns
    -- not a persistent feature, just a way to exercise push()/end()
    live. Normal desktop audio resumes automatically afterward."""
    while hw._gst_sender is None or not hw._gst_sender._receiver_ip:
        await asyncio.sleep(poll_interval)

    gst_sender = hw._gst_sender
    log.info(f'[audio-stream-demo] streaming a {freq_hz}Hz test tone to the brain '
            f'in {chunk_dur}s chunks, {seconds}s total...')
    handle = gst_sender.start_audio_stream(sample_rate=sample_rate)
    if handle is None:
        log.error('[audio-stream-demo] could not start the audio stream')
        return

    t0 = 0.0
    n_chunks = int(seconds / chunk_dur)
    for _ in range(n_chunks):
        t = t0 + np.arange(0, chunk_dur, 1.0 / sample_rate)
        chunk = (0.3 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
        handle.push(chunk)
        t0 += chunk_dur
        await asyncio.sleep(chunk_dur)
    handle.end()
    log.info('[audio-stream-demo] done -- normal desktop audio resumes automatically')


if __name__ == '__main__':
    radio = RobotRadio(endpoint_type='desktop')
    try:
        hw = DesktopHw()
    except DesktopCaptureError as e:
        print(f'\n[desktop_endpoint] {e}\n', file=sys.stderr)
        sys.exit(1)

    main_node = RobotNode(radio, hw)

    async def _run_with_audio_stream_demo():
        await asyncio.gather(main_node.run(), audio_stream_demo(hw))

    asyncio.run(_run_with_audio_stream_demo())
