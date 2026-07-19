"""
examples/shmem_bridge_ai.py -- a stand-in AI process attaching to the
shared memory bridge started by examples/shmem_bridge_robonet.py.

Run shmem_bridge_robonet.py first (or at the same time -- this retries
until it's up, since a real AI process spinning up shouldn't require
robonet to already be waiting for it, and vice versa).

Demonstrates: attaching to named channels without hardcoding their
shared-memory names or sizes (that comes from the handshake), reading
continuously-updated data with the seqlock's no-torn-reads guarantee,
and sending a status message back over the control connection. A real
AI process would replace the "print what we got" loop below with
actually feeding this data to a model.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from robonet.bridge.bridge_control import BridgeClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')
log = logging.getLogger(__name__)

VIDEO_WIDTH, VIDEO_HEIGHT = 320, 240


def main():
    client = BridgeClient()
    log.info('[ai] connecting to the robonet bridge...')
    ok = client.connect(timeout_s=None)  # waits indefinitely -- robonet may not be up yet
    if not ok:
        log.error('[ai] could not connect')
        return
    log.info(f'[ai] connected -- channels available: {list(client.channels.keys())}')

    client.send({'ai_running': True, 'want_control': 'human'})

    last_report = 0.0
    frames_seen = 0
    audio_chunks_seen = 0
    try:
        while True:
            video_bytes = client.channels['video'].read()
            if video_bytes is not None:
                frame = np.frombuffer(video_bytes, dtype=np.uint8).reshape(
                    VIDEO_HEIGHT, VIDEO_WIDTH, 3)
                frames_seen += 1
                # A real AI would consume `frame` here. This just
                # confirms it's actually changing over time.
                center_pixel = frame[VIDEO_HEIGHT // 2, VIDEO_WIDTH // 2]

            audio_bytes = client.channels['audio'].read()
            if audio_bytes is not None:
                audio = np.frombuffer(audio_bytes, dtype=np.float32)
                audio_chunks_seen += 1

            msg = client.recv(timeout_s=0.0)
            if msg is not None:
                log.info(f'[ai] received from robonet: {msg}')

            now = time.time()
            if now - last_report > 2.0:
                log.info(f'[ai] frames seen: {frames_seen}  audio chunks seen: {audio_chunks_seen}  '
                        f'center pixel: {center_pixel if video_bytes is not None else "n/a"}')
                last_report = now

            time.sleep(1.0 / 60)  # poll faster than the writer to avoid missing updates
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        log.info('[ai] disconnected')


if __name__ == '__main__':
    main()
