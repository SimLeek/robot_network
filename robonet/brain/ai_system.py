# todo: need to complete a working version

from enum import Enum

from robonet.brain.util.system_base import SubSystem


class AISubSystem(SubSystem):
    """
    This connects to an AI through zmq, has an AI, etc., reads its output, and feeds its context.

    It is still an abstract class and needs to be subclassed.
    """
    class OutMode(Enum):
        NEURON = 1
        TOKEN = 2

    def __init__(self):
        self.in_img = None
        self.out_mode = self.OutMode.NEURON

    def update_frame(self, img):
        self.in_img = img
