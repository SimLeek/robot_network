"""
robonet/audio_io.py

The audio equivalent of what cam_res/cam_fps already are for video:
settings that double as "how many neurons does the AI need, and at what
rate." One audio callback block = one neuron vector; the callback fires
at sample_rate/blocksize Hz. Lives at the top level (like util.py,
logging_setup.py) since this is plain math, not specific to the brain or
endpoint side -- whichever side ends up owning the actual virtual-device
plumbing, both sides can size their blocksize against the same formula.
"""

from __future__ import annotations

import math
from typing import NamedTuple


class AudioNeuronSpec(NamedTuple):
    """count: neurons per callback (== blocksize, one neuron per sample).
    hz: how often the callback fires (== sample_rate / blocksize)."""
    count: int
    hz: float


def audio_neuron_spec(sample_rate: int, blocksize: int) -> AudioNeuronSpec:
    """Neuron count and callback rate for an audio channel with this
    sample_rate/blocksize -- one neuron per sample in the block, firing
    at sample_rate/blocksize Hz."""
    if blocksize <= 0:
        raise ValueError(f'blocksize must be positive, got {blocksize}')
    return AudioNeuronSpec(count=blocksize, hz=sample_rate / blocksize)


def blocksize_for_max_hz(sample_rate: int, max_hz: float) -> int:
    """The other direction: if the AI can only process up to max_hz
    callbacks/second, this is the smallest blocksize that keeps the
    actual callback rate at or under that ceiling (rounds up -- a larger
    block means a slower, not faster, callback rate)."""
    if max_hz <= 0:
        raise ValueError(f'max_hz must be positive, got {max_hz}')
    return math.ceil(sample_rate / max_hz)
