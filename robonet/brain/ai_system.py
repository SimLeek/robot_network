# todo: need to complete a working version

from enum import Enum

from robonet.brain.util.system_base import SubSystem


class AISubSystem(SubSystem):
    """
    This connects to an AI through zmq, has an AI, etc., reads its output, and feeds its context.

    It is still an abstract class and needs to be subclassed.

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

    def update_frame(self, img):
        self.in_img = img
        self.has_video = True

    def update_audio(self, aud):
        self.in_aud = aud
        self.has_audio = True
