import asyncio
import typing
from typing import Dict, Optional, Tuple

import numpy as np

from robonet.brain.util.system_base import SubSystem
from robonet.buffers.buffer_objects import (WhoAreYou, WhoAreYouAck, RobotCapabilities, RobotCapabilitiesAck,
                                            RobotStart,
                                            TensorBuffer, SparseVectorBuffer)

from robonet.logging_setup import setup_logging

log = setup_logging()

if typing.TYPE_CHECKING:
    from robonet.brain.util.network_scanner import Endpoint
    from robonet.brain.main_system import ServerSystem

class RobotSubSystem(SubSystem):
    """
    Server-side robot counterpart.

    Handshake:   → WhoAreYouAck → RobotCapabilities(probe)
                ← RobotCapabilities(populated) → RobotCapabilitiesAck + RobotStart

    Control:    keyboard keys defined in axis capabilities → TensorBuffer at CTRL_HZ
                Keys are press/release: value is set on press, cleared to 0.0 on release.
    """
    _endpoint_type = 'robot'
    _val_thresh = 0.01
    CTRL_HZ = 30

    def __init__(self, endpoint: 'Endpoint'):
        self._endpoint = endpoint
        self._root = None
        self._running = False
        self._key_map: Dict[int, list] = {}
        self._axis: Dict[int, float] = {}
        self._n_neurons = 0
        self._kb_handle = None             # for unbinding on teardown
        self.handlers = {}

    def setup(self, root: 'ServerSystem'):
        self._root = root

    def start(self):
        self._running = True
        self._build_key_map(self._endpoint.axes or [])
        self._bind_keys()

    def stop(self):
        self._running = False
        self._unbind_keys()
        # zero out robot on disconnect
        if self._n_neurons > 0:
            self._root.radio.burst(
                SparseVectorBuffer(np.asarray([], dtype=np.uint32), np.asarray([], dtype=np.float32)))

    # ── control ───────────────────────────────────────────────────────

    def _build_key_map(self, axes: list):
        max_n = 0
        for a in axes:
            n = int(a.get('neuron', 0))
            max_n = max(max_n, n)
            self._axis[n] = 0.0
            for kv in a.get('keys', []):
                kc = int(kv[0])
                self._key_map.setdefault(kc, []).append((n, float(kv[1])))
        self._n_neurons = max_n + 1

    def _bind_keys(self):
        if self._root.displayer is not None:
            self._root.displayer.af_thru.bind_keyboard(self._on_key_event)
            self._kb_handle = self._on_key_event

    def _unbind_keys(self):
        if self._root and self._root.displayer and self._kb_handle:
            self._root.displayer.af_thru.unbind_keyboard(self._kb_handle)
            self._kb_handle = None

    def _on_key_event(self, key, action, modifiers):
        d = self._root.displayer
        if d is None or self._root.menu.visible:
            return  # don't send control while menu is open
        wkeys = d.displayer.displayer.config.wnd.keys
        if action == wkeys.ACTION_PRESS:
            for n, v in self._key_map.get(key, []):
                self._axis[n] = v
        elif action == wkeys.ACTION_RELEASE:
            for n, _ in self._key_map.get(key, []):
                self._axis[n] = 0.0

    def _ctrl_vec(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        idx = []
        val = []
        for n, v in self._axis.items():
            if n < self._n_neurons and abs(v)>self._val_thresh:
                idx.append(n)
                val.append(v)
        if idx:
            idx = np.asarray(idx, dtype=np.uint32)
            val = np.asarray(val, dtype=np.float32)
            return idx, val
        else:
            return None

    # ── async loops ───────────────────────────────────────────────────

    async def _control_loop(self):
        dt = 1.0 / self.CTRL_HZ
        while self._running:
            if self._n_neurons > 0:
                vals = self._ctrl_vec()
                if vals is not None:
                    self._root.radio.burst(SparseVectorBuffer(*vals))
                else:
                    # keepalive hack
                    self._root.radio.burst(SparseVectorBuffer(np.asarray([], dtype=np.uint32), np.asarray([], dtype=np.float32)))
            await asyncio.sleep(dt)

    def async_loops(self, sm: 'ServerSystem'):
        return [self._control_loop()]
