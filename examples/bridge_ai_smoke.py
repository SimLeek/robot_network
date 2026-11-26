"""
examples/bridge_ai_smoke.py -- checks the whole AI path on one machine.

Start, in this order or any order:
    python -m examples.desktop.desktop_endpoint                     # endpoint (DISPLAY=:0)
    python -m robonet.brain.main --headless --monitor-dir /tmp/mon  # brain, auto-connects to localhost
    python -m examples.bridge_ai_smoke --out /tmp/smoke

Then: connects to the bridge, waits for video, takes control, moves the
mouse a little, taps a key, presses F11 (should be refused by the
endpoint if blocked_key_combos lists it -- check the endpoint log),
saves the first and last frame it received, gives control back, and
prints a JSON summary.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np

from robonet.bridge.ai_client_base import RobonetAIClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')
log = logging.getLogger(__name__)


class SmokeAI(RobonetAIClient):
    def __init__(self, key: str, press_f11: bool, **kw):
        super().__init__(**kw)
        self.key, self.press_f11 = key, press_f11
        self.endpoint = None
        self.frames = 0
        self.audio_chunks = 0
        self.first = self.last = None
        self.t0 = None
        self.script = None
        self.done = False

    def on_robonet_start(self): log.info('[smoke] robonet running')
    def on_robonet_shutdown(self): log.info('[smoke] robonet shutting down')
    def on_robonet_connect(self, name): self.endpoint = name; log.info(f'[smoke] endpoint {name}')
    def on_robonet_disconnect(self): self.endpoint = None

    def on_channel_data(self, label, data):
        info = self.channel_info.get(label)
        if info is None:
            return
        arr = np.frombuffer(data, dtype=info['dtype'])
        if label == 'video':
            if arr.size != int(np.prod(info['shape'])):
                return
            self.last = arr.reshape(info['shape']).copy()
            if self.first is None:
                self.first = self.last
            self.frames += 1
        elif label == 'audio':
            self.audio_chunks += 1

    def _steps(self):
        k = self.key
        yield 0.5, lambda: self.set_want_control('ai')
        for i in range(10):
            yield 0.05, (lambda i=i: self.send_mouse_move(0.5 + 0.01 * i, 0.5))
        yield 0.2, lambda: self.send_key(k, True)
        yield 0.15, lambda: self.send_key(k, False)
        if self.press_f11:
            yield 0.2, lambda: self.send_key('f11', True)
            yield 0.1, lambda: self.send_key('f11', False)
        yield 1.0, lambda: self.set_want_control('human')

    def on_tick(self):
        if self.done or self.frames < 5:
            return
        if self.script is None:
            self.script = self._steps()
            self.next_at = time.time()
        if time.time() >= self.next_at:
            try:
                delay, act = next(self.script)
            except StopIteration:
                self.done = True
                self.stop()
                return
            act()
            self.next_at = time.time() + delay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=Path('/tmp/robonet_smoke'))
    ap.add_argument('--key', default='e', help='key to tap (e opens the Minecraft inventory)')
    ap.add_argument('--no-f11', action='store_true')
    ap.add_argument('--timeout', type=float, default=60.0)
    a = ap.parse_args()
    ai = SmokeAI(a.key, not a.no_f11)
    if not ai.connect(timeout_s=a.timeout):
        raise SystemExit('could not reach the robonet bridge')
    t0 = time.time()
    import threading
    timer = threading.Timer(a.timeout, ai.stop)
    timer.daemon = True
    timer.start()
    ai.run()
    a.out.mkdir(parents=True, exist_ok=True)
    import cv2
    for name, img in (('first', ai.first), ('last', ai.last)):
        if img is not None:
            cv2.imwrite(str(a.out / f'{name}.png'), img)
    print(json.dumps({'endpoint': ai.endpoint, 'frames': ai.frames, 'audio_chunks': ai.audio_chunks,
                      'fps': round(ai.frames / (time.time() - t0), 2), 'script_done': ai.done,
                      'channel_info': ai.channel_info, 'brain_health': ai.brain_health}, default=str))
    ai.close()
    raise SystemExit(0 if ai.done else 1)


if __name__ == '__main__':
    main()
