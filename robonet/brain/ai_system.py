# todo: need to complete a working version

from enum import Enum
from typing import Optional

from robonet.brain.util.system_base import SubSystem


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

    def setup(self, root):
        self._root = root

    def start(self):
        """Subclasses overriding this should call super().start() so
        an attached bridge actually gets started."""
        if self.bridge is not None:
            self.bridge.start()

    def stop(self):
        if self.bridge is not None:
            self.bridge.stop()

    def async_loops(self, sm):
        """No coroutines of its own by default -- the bridge runs its
        own background thread internally, not asyncio. Subclasses with
        real async work (e.g. a demo sequence) override this."""
        return []

    def update_frame(self, img):
        self.in_img = img
        self.has_video = True
        if self.bridge is not None:
            self.bridge.write_channel('video_frame', 'video', img.tobytes())

    def update_audio(self, aud):
        self.in_aud = aud
        self.has_audio = True
        if self.bridge is not None:
            self.bridge.write_channel('audio_chunk', 'audio', aud.tobytes())

    def update_selection_text(self, text: str):
        self.selection_text = text
        self.has_selection_text = True
        if self.bridge is not None:
            self.bridge.send({'event': 'selection_text', 'text': text})
