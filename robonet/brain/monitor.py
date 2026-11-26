"""
robonet/brain/monitor.py -- watch a headless brain from a shell.

Every interval_s, writes into out_dir (atomically, via rename):
  frame.png    the exact frame the AI is sent (viewport + menu overlay)
  status.json  endpoint, input source, AI bridge state, frame/action
               rates, audio level, held keys, the last 50 AI actions
and appends one line per interval to status.jsonl.

    watch -n1 cat ~/.robobrain/monitor/status.json
    scp bee:~/.robobrain/monitor/frame.png .
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np


class Monitor:
    def __init__(self, root, out_dir: Path, interval_s: float = 1.0):
        self.root = root
        self.out = Path(out_dir)
        self.interval_s = interval_s
        self.out.mkdir(parents=True, exist_ok=True)
        self._last = None  # (time, frames_sent, total_actions)

    def _write_atomic(self, name: str, data: bytes) -> None:
        tmp = self.out / (name + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, self.out / name)

    def snapshot(self) -> dict:
        r, ai = self.root, self.root.ai
        sub = r.active_sub
        ep = getattr(sub, '_endpoint', None)
        now = time.time()
        actions = sum(ai.action_counts.values()) if ai is not None else 0
        frames = ai.frames_sent if ai is not None else 0
        fps = aps = None
        if self._last is not None:
            dt = now - self._last[0]
            fps = round((frames - self._last[1]) / dt, 2)
            aps = round((actions - self._last[2]) / dt, 2)
        self._last = (now, frames, actions)
        aud = getattr(r.menu, 'last_audio', None)
        rms = float(np.sqrt(np.mean(np.square(aud.astype(np.float32))))) if aud is not None and aud.size else None
        bridge = ai.bridge if ai is not None else None
        return {
            't': round(now, 3),
            'endpoint': (getattr(ep, 'hostname', None) or getattr(ep, 'ip', None)) if ep else None,
            'endpoint_type': type(sub).__name__ if sub is not None else None,
            'input_source': getattr(sub, 'input_source', None),
            'ai_bridge_connected': bool(bridge and bridge.connected),
            'ai_alive': bool(bridge and bridge.ai_is_alive(getattr(ai, 'ai_timeout_s', 5.0))),
            'ai_wants': getattr(ai, 'wanted_source', None),
            'frames_to_ai_per_s': fps,
            'ai_actions_per_s': aps,
            'ai_action_counts': dict(getattr(ai, 'action_counts', {})),
            'held_keys': sorted(getattr(sub, '_held_keys_brain', {}) or {}),
            'audio_rms': None if rms is None else round(rms, 5),
            'frame_shape': list(ai.in_img.shape) if ai is not None and ai.in_img is not None else None,
            'menu_visible': bool(getattr(r.menu, 'visible', False)),
            'recent_actions': list(getattr(ai, 'recent_actions', []))[-50:],
        }

    def write_once(self) -> dict:
        st = self.snapshot()
        img = self.root.ai.in_img if self.root.ai is not None else None
        if img is not None:
            ok, png = cv2.imencode('.png', img)
            if ok:
                self._write_atomic('frame.png', png.tobytes())
        self._write_atomic('status.json', json.dumps(st, indent=1, default=str).encode())
        brief = {k: v for k, v in st.items() if k != 'recent_actions'}
        with open(self.out / 'status.jsonl', 'a') as fh:
            fh.write(json.dumps(brief, default=str) + '\n')
        return st

    async def run(self):
        while True:
            try:
                self.write_once()
            except Exception as e:  # monitoring must never take the brain down
                print(f'[monitor] write failed: {e}', flush=True)
            await asyncio.sleep(self.interval_s)
