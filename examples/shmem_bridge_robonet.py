"""
examples/shmem_bridge_robonet.py -- the robonet/brain side of the
shared memory bridge to a separate AI process.

Run this first, then examples/shmem_bridge_ai.py (in a separate
terminal, or on a separate machine's Python -- though today this only
works locally, since it's real shared memory, not network transport).

This demonstrates the bridge mechanism itself: two named channels
('video', 'audio') carrying continuously-written synthetic data, plus
bidirectional status messages over the control connection. It does
NOT yet pull real frames from a live DesktopSubSystem/AISubSystem --
that wiring, and the actual brain<->AI status signals and lifecycle
callbacks, are separate, later pieces (see branch_todo.md). This is
the transport layer they'll all sit on top of.

Safe to kill and restart at any time -- the bridge keeps listening for
a fresh AI process if the current one disconnects or crashes, and this
script itself keeps running if the AI side disconnects or crashes.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from robonet.bridge.bridge_control import BridgeServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')
log = logging.getLogger(__name__)

VIDEO_WIDTH, VIDEO_HEIGHT = 320, 240
VIDEO_CAPACITY = VIDEO_WIDTH * VIDEO_HEIGHT * 3  # uncompressed RGB, matches the actual frame size
AUDIO_CAPACITY = 48000 * 4 // 10  # ~0.1s of float32 mono at 48kHz


def make_synthetic_frame(t: float) -> np.ndarray:
    """A moving gradient -- enough to visibly confirm frames are
    actually changing over time on the receiving side, without needing
    a real video source for this demonstration."""
    x = np.linspace(0, 1, VIDEO_WIDTH)
    y = np.linspace(0, 1, VIDEO_HEIGHT)
    xv, yv = np.meshgrid(x, y)
    shift = (t * 0.2) % 1.0
    r = ((xv + shift) % 1.0 * 255).astype(np.uint8)
    g = (yv * 255).astype(np.uint8)
    b = np.full_like(r, 128)
    return np.stack([r, g, b], axis=-1)


def make_synthetic_audio(t: float, sample_rate: int = 48000, chunk_s: float = 0.1) -> np.ndarray:
    n = int(sample_rate * chunk_s)
    samples = t + np.arange(n) / sample_rate
    return (0.3 * np.sin(2 * np.pi * 440.0 * samples)).astype(np.float32)


def main():
    server = BridgeServer()
    server.create_channel('video_frame', capacity_bytes=VIDEO_CAPACITY, label='video')
    server.create_channel('audio_chunk', capacity_bytes=AUDIO_CAPACITY, label='audio')
    server.start()

    t0 = time.time()
    last_status_log = 0.0
    try:
        while True:
            t = time.time() - t0
            server.channels['video_frame'].write(make_synthetic_frame(t).tobytes())
            server.channels['audio_chunk'].write(make_synthetic_audio(t).tobytes())

            msg = server.recv(timeout_s=0.0)
            if msg is not None:
                log.info(f'[robonet] received from AI: {msg}')

            if t - last_status_log > 2.0:
                log.info(f'[robonet] AI connected: {server.connected}')
                server.send({'brain_healthy': True, 'connected_endpoint': None})
                last_status_log = t

            time.sleep(1.0 / 30)  # ~30fps
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        log.info('[robonet] stopped, shared memory released')


if __name__ == '__main__':
    main()
