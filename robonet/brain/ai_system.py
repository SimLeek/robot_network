# todo: need to complete a working version

import asyncio
import collections
import logging
import time
from enum import Enum
from typing import Optional

from robonet.brain.util.system_base import SubSystem

log = logging.getLogger(__name__)


class AISubSystem(SubSystem):
    """
    This connects to an AI, has an AI, etc., reads its output, and feeds its context.

    A bridge to a real, separate AI process is optional (see
    robonet.bridge.bridge_control.BridgeServer) -- self.bridge, started/
    stopped alongside this subsystem itself. update_frame/update_audio/
    update_selection_text store locally for an in-process AI
    (subclass this for one) and forward through self.bridge if one's
    attached, so a subclass doesn't need to know or care whether the
    actual AI is in-process or a separate process on the other end of
    a bridge.

    Subclass this for an in-process AI's own behavior; used as-is,
    with just a bridge attached, it's a "no in-process AI, just a
    bridge to a real external one" host.

    in_aud: mono (N,) or stereo (N, 2), float32 in [-1, 1] audio
    """
    class OutMode(Enum):
        NEURON = 1
        TOKEN = 2

    def __init__(self):
        self.in_img = None
        self.in_aud = None
        self.has_video = False
        self.has_audio = False
        self.out_mode = self.OutMode.NEURON
        self.bridge: Optional['BridgeServer'] = None
        self.selection_text: Optional[str] = None
        self.has_selection_text = False
        self._root: 'ServerSystem' = None
        self._running = False
        self._shapes: dict = {}
        self.wanted_source = 'human'
        self.ai_timeout_s = 5.0  # no heartbeat for this long -> control goes back to the human
        self._ai_held_keys: set = set()
        self._ai_held_buttons: set = set()
        self._notified_sub = None
        self.frames_sent = 0
        self.action_counts: dict = {}
        self.recent_actions = collections.deque(maxlen=50)

    _ACTION_EVENTS = ('key', 'mouse_move', 'mouse_button', 'scroll', 'token')

    def setup(self, root):
        self._root = root

    def start(self):
        """Subclasses overriding this should call super().start() so
        an attached bridge actually gets started."""
        self._running = True
        if self.bridge is not None:
            self.bridge.start()

    def stop(self):
        self._running = False
        if self.bridge is not None:
            self.bridge.stop()

    def async_loops(self, sm):
        """With a bridge attached: the loop that applies the AI's
        control requests and actions. Subclasses with real async work
        (e.g. a demo sequence) override this."""
        return [self._bridge_loop()] if self.bridge is not None else []

    def update_frame(self, img):
        self.in_img = img
        self.has_video = True
        self.frames_sent += 1
        if self.bridge is not None:
            self.bridge.write_channel('video_frame', 'video', img.tobytes())
            self._announce_shape('video', img)

    def update_audio(self, aud):
        self.in_aud = aud
        self.has_audio = True
        if self.bridge is not None:
            self.bridge.write_channel('audio_chunk', 'audio', aud.tobytes())
            self._announce_shape('audio', aud)

    def _announce_shape(self, label, arr):
        info = (tuple(arr.shape), str(arr.dtype))
        if self._shapes.get(label) != info:
            self._shapes[label] = info
            self.bridge.send_sticky(f'frame_info:{label}', {
                'event': 'frame_info', 'label': label, 'shape': list(info[0]), 'dtype': info[1]})

    # -- bridge -> robonet: control requests and actions from the AI process

    def _desktop(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        sub = self._root.active_sub if self._root is not None else None
        return sub if isinstance(sub, DesktopSubSystem) else None

    def _record(self, msg):
        self.action_counts[msg.get('event')] = self.action_counts.get(msg.get('event'), 0) + 1
        self.recent_actions.append({'t': round(time.time(), 3), **msg})

    def handle_bridge_message(self, msg) -> None:
        """Apply one message from the AI process. Actions only reach the
        endpoint while the desktop's input_source is 'ai'."""
        if not isinstance(msg, dict):
            return
        event = msg.get('event')
        if event == 'want_control':
            self.wanted_source = 'ai' if msg.get('value') == 'ai' else 'human'
            return
        if event not in self._ACTION_EVENTS:
            return
        desk = self._desktop()
        if desk is None:
            return
        self._record(msg)
        if event == 'key':
            if msg.get('pressed'):
                desk.ai_key_press(msg['key'], msg.get('modifiers', ''))
                self._ai_held_keys.add(msg['key'])
            else:
                desk.ai_key_release(msg['key'], msg.get('modifiers', ''))
                self._ai_held_keys.discard(msg['key'])
        elif event == 'mouse_move':
            desk.ai_mouse_move(float(msg['x']), float(msg['y']))
        elif event == 'mouse_button':
            if msg.get('pressed'):
                desk.ai_mouse_press(msg.get('button', 'left'))
                self._ai_held_buttons.add(msg.get('button', 'left'))
            else:
                desk.ai_mouse_release(msg.get('button', 'left'))
                self._ai_held_buttons.discard(msg.get('button', 'left'))
        elif event == 'scroll':
            desk.ai_scroll(int(msg['delta']))
        elif event == 'token':
            desk.af_ai.on_token(int(msg['token']))

    def release_ai_inputs(self) -> None:
        """Let go of everything the AI is holding (AI died, hung, or gave
        control back) -- otherwise the brain's key-refresh loop would keep
        a dead AI's keys held down forever."""
        desk = self._desktop()
        for key in list(self._ai_held_keys):
            if desk is not None:
                desk.ai_key_release(key)
        for button in list(self._ai_held_buttons):
            if desk is not None:
                desk.ai_mouse_release(button)
        self._ai_held_keys.clear()
        self._ai_held_buttons.clear()

    def _apply_control(self) -> None:
        desk = self._desktop()
        alive = self.bridge is not None and self.bridge.connected and self.bridge.ai_is_alive(self.ai_timeout_s)
        source = self.wanted_source if alive else 'human'
        if desk is not None and desk.input_source != source:
            if source == 'human':
                self.release_ai_inputs()  # while still 'ai', so the releases are sent
            desk.set_input_source(source)
            log.info(f'[ai] input source -> {source}')
        if not alive and self.wanted_source == 'ai':
            self.wanted_source = 'human'  # a reconnecting AI has to ask again

    def _notify_endpoint(self) -> None:
        sub = self._root.active_sub if self._root is not None else None
        if sub is self._notified_sub:
            return
        self._notified_sub = sub
        if sub is None:
            self.bridge.notify_disconnect()
        else:
            ep = getattr(sub, '_endpoint', None)
            self.bridge.notify_connect(getattr(ep, 'hostname', '') or getattr(ep, 'ip', '') or type(sub).__name__)

    async def _bridge_loop(self, poll_s: float = 0.01, health_every_s: float = 2.0):
        last_health = 0.0
        while self._running:
            while True:
                msg = self.bridge.recv()
                if msg is None:
                    break
                self.handle_bridge_message(msg)
            self._apply_control()
            self._notify_endpoint()
            now = time.time()
            if now - last_health >= health_every_s:
                self.bridge.compute_and_send_health()
                last_health = now
            await asyncio.sleep(poll_s)

    def update_selection_text(self, text: str):
        self.selection_text = text
        self.has_selection_text = True
        if self.bridge is not None:
            self.bridge.send({'event': 'selection_text', 'text': text})
