"""
examples/shmem_bridge_ai.py -- a stand-in AI process attaching to the
shared memory bridge started by examples/shmem_bridge_robonet.py.

Run shmem_bridge_robonet.py first (or at the same time -- connect()
retries until it's up, since a real AI process spinning up shouldn't
require robonet to already be waiting for it, and vice versa).

Demonstrates RobonetAIClient: subclass it, implement the four required
lifecycle callbacks, override on_channel_data for the actual per-frame/
per-chunk work, then connect() and run().
"""

from __future__ import annotations

import logging
import time

import numpy as np

from robonet.bridge.ai_client_base import RobonetAIClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')
log = logging.getLogger(__name__)

VIDEO_WIDTH, VIDEO_HEIGHT = 320, 240


class DemoAIClient(RobonetAIClient):
    """A stand-in for a real AI: just logs what it receives and how
    much of it, on a periodic tick, to make actually-changing data
    over time (not just a live connection) visible."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.frames_seen = 0
        self.audio_chunks_seen = 0
        self._last_center_pixel = None
        self._last_report = 0.0

    def on_robonet_start(self) -> None:
        log.info('[ai] robonet is running')

    def on_robonet_shutdown(self) -> None:
        log.info('[ai] robonet is shutting down')

    def on_robonet_connect(self, endpoint_name) -> None:
        log.info(f'[ai] robonet connected to endpoint: {endpoint_name}')
        self.set_want_control('human')  # this demo just watches -- a real AI would decide for itself

    def on_robonet_disconnect(self) -> None:
        log.info('[ai] robonet disconnected from its endpoint')

    def on_robonet_health(self, health) -> None:
        log.info(f'[ai] robonet health: {health}')

    def on_selection_text(self, text: str) -> None:
        log.info(f'[ai] selection text received: {text!r}')

    def on_channel_data(self, label: str, data: bytes) -> None:
        if label == 'video':
            frame = np.frombuffer(data, dtype=np.uint8).reshape(VIDEO_HEIGHT, VIDEO_WIDTH, 3)
            self.frames_seen += 1
            # A real AI would consume `frame` here. This just confirms
            # it's actually changing over time.
            self._last_center_pixel = frame[VIDEO_HEIGHT // 2, VIDEO_WIDTH // 2]
        elif label == 'audio':
            np.frombuffer(data, dtype=np.float32)  # a real AI would consume this
            self.audio_chunks_seen += 1

    def on_tick(self) -> None:
        now = time.time()
        if now - self._last_report > 2.0:
            log.info(f'[ai] frames seen: {self.frames_seen}  '
                    f'audio chunks seen: {self.audio_chunks_seen}  '
                    f'center pixel: {self._last_center_pixel}')
            self._last_report = now


def main():
    client = DemoAIClient()
    log.info('[ai] connecting to the robonet bridge...')
    if not client.connect(timeout_s=None):  # waits indefinitely -- robonet may not be up yet
        log.error('[ai] could not connect')
        return
    log.info(f'[ai] connected -- channels available: {list(client.bridge.channels.keys())}')
    client.bridge.send({'ai_running': True, 'want_control': 'human'})

    try:
        client.run()
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        log.info('[ai] disconnected')


if __name__ == '__main__':
    main()
