"""
robonet/bridge/shmem_channel.py

A single named, fixed-capacity byte channel backed by real shared
memory (multiprocessing.shared_memory) between two separate OS
processes -- not zmq, not sockets for the data itself, actual shared
memory (confirmed faster for this than either).

One writer, any number of readers, no mutex/semaphore needed: uses a
seqlock (the same pattern the Linux kernel uses for exactly this
problem -- a single writer, readers that must never see a torn value,
and neither side allowed to block the other). The writer bumps a
generation counter to odd before writing and back to even after; a
reader that sees an odd generation, or sees the generation change
between the start and end of its own read, knows the read may have
raced a write and retries. The writer never waits on a reader. A
reader might retry a few times in the rare case it races the writer,
but never returns a torn/partial payload -- the AI deserves not to
have screen tearing.

Double or triple buffering was the other standard option here, but
needs the same kind of atomically-updated "which slot is current"
index anyway, so it doesn't actually avoid the underlying problem --
it just moves it. A single buffer plus a generation counter solves it
directly, with less bookkeeping.
"""

from __future__ import annotations

import logging
import time
from multiprocessing.shared_memory import SharedMemory
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# generation: bumped odd->even around each write. length: how many of
# the payload bytes are actually valid (a channel's declared capacity
# is a maximum, not every write need fill it). last_write_time: wall-
# clock seconds at the last completed write -- the direct basis for
# "is this channel actually still being updated" (a reader compares it
# to time.time() itself; framerate is a rate of change of this, tracked
# by whoever wants it, not stored here).
_HEADER_DTYPE = np.dtype([('generation', np.int64), ('length', np.int64),
                          ('last_write_time', np.float64)])
_HEADER_SIZE = _HEADER_DTYPE.itemsize  # 24 bytes, aligned -- int64/float64 writes are
                                       # single CPU instructions on any
                                       # platform this actually runs on, so
                                       # each field update is atomic on its
                                       # own; the seqlock is what makes the
                                       # whole group (length + timestamp +
                                       # payload) consistent together.


class ShmemChannel:
    """One named byte channel. Construct with create=True on the side
    that owns/originates the data (creates the segment, cleans it up),
    create=False on the attaching side (fails with FileNotFoundError
    if the creator hasn't made it yet -- callers should retry)."""

    def __init__(self, name: str, capacity_bytes: int, create: bool, label: str = ''):
        self.name = name
        self.capacity_bytes = capacity_bytes
        self.label = label  # human/AI-readable hint (e.g. 'video', 'audio') -- not load-bearing for the channel itself
        total_size = _HEADER_SIZE + capacity_bytes
        self._owns_segment = create
        if create:
            self._shm = SharedMemory(name=name, create=True, size=total_size)
        else:
            self._shm = SharedMemory(name=name, create=False)
            if self._shm.size < total_size:
                self._shm.close()
                raise ValueError(f"shared memory segment {name!r} is smaller "
                                f"({self._shm.size}B) than this channel expects "
                                f"({total_size}B) -- capacity_bytes must match "
                                f"on both sides")
        self._header = np.ndarray(1, dtype=_HEADER_DTYPE, buffer=self._shm.buf[:_HEADER_SIZE])
        self._payload = self._shm.buf[_HEADER_SIZE:_HEADER_SIZE + capacity_bytes]
        if create:
            self._header['generation'][0] = 0
            self._header['length'][0] = 0
            self._header['last_write_time'][0] = 0.0

    def write(self, data: bytes) -> None:
        """Never blocks. Raises ValueError if data exceeds capacity --
        callers should size channels for their real maximum payload
        (e.g. an uncompressed frame at the configured resolution)."""
        if len(data) > self.capacity_bytes:
            raise ValueError(f"write of {len(data)}B exceeds channel "
                            f"{self.name!r}'s capacity of {self.capacity_bytes}B")
        gen = int(self._header['generation'][0])
        self._header['generation'][0] = gen + 1  # odd -- readers now know a write is in progress
        self._payload[:len(data)] = data
        self._header['length'][0] = len(data)
        self._header['last_write_time'][0] = time.time()
        self._header['generation'][0] = gen + 2  # even again -- write complete, consistent

    def write_count(self) -> int:
        """How many completed writes so far (the generation counter
        increments by 2 per write -- odd during, even after)."""
        return int(self._header['generation'][0]) // 2

    def seconds_since_write(self) -> Optional[float]:
        """None if nothing has ever been written. The direct basis for
        "is this channel actually still being updated" -- a large
        value means it's gone stale, regardless of whatever the
        writer's intended rate was."""
        last = float(self._header['last_write_time'][0])
        if last == 0.0:
            return None
        return time.time() - last

    def read(self, max_retries: int = 50, retry_sleep_s: float = 0.0005) -> Optional[bytes]:
        """Returns the most recent complete write, or None if nothing
        has been written yet, or (rarely) if the writer was unusually
        busy across every retry -- callers should just try again next
        tick rather than treat that as an error."""
        for _ in range(max_retries):
            g1 = int(self._header['generation'][0])
            if g1 % 2 == 1:
                time.sleep(retry_sleep_s)
                continue
            length = int(self._header['length'][0])
            if length == 0:
                return None  # nothing written yet
            data = bytes(self._payload[:length])
            g2 = int(self._header['generation'][0])
            if g1 == g2:
                return data
            time.sleep(retry_sleep_s)
        log.warning(f"[shmem] {self.name!r}: gave up after {max_retries} retries "
                   f"without a stable read -- writer unusually busy")
        return None

    def close(self) -> None:
        """Detaches this process's view. Safe to call from both the
        creating and attaching side (only the creator should also
        call unlink()), and safe to call more than once -- releases
        the header/payload views first, since the underlying
        SharedMemory can't close while a numpy array or memoryview
        still has the buffer exported."""
        if self._header is None:
            return  # already closed
        self._payload.release()
        self._header = None
        self._shm.close()

    def unlink(self) -> None:
        """Releases the underlying OS resource. Only the creating side
        should call this, and only once every attached side is done --
        matches multiprocessing.shared_memory's own contract."""
        if not self._owns_segment:
            log.warning(f"[shmem] unlink() called on {self.name!r} from a "
                       f"non-owning attachment -- only the creator should do this")
        self._shm.unlink()
