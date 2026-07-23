"""
tests/test_shmem_channel.py

Tests ShmemChannel: basic write/read behavior in a single process, and
-- the property that actually matters -- that the seqlock genuinely
prevents torn reads under real, concurrent, multi-process access. No
mocking multiprocessing.shared_memory here; it's pure standard library
and runs fine in any sandbox, so there's no reason not to test it for
real.
"""

import multiprocessing as mp
import struct
import time
import unittest
import uuid

from robonet.bridge.shmem_channel import ShmemChannel


def _unique_name(prefix: str) -> str:
    # Avoids collisions between test runs / parallel test processes --
    # a stale segment left behind by a crashed prior run must not
    # break a later one.
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


class TestShmemChannelBasics(unittest.TestCase):

    def test_read_before_any_write_is_none(self):
        name = _unique_name('empty')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            self.assertIsNone(ch.read())
        finally:
            ch.close()
            ch.unlink()

    def test_write_then_read_round_trips(self):
        name = _unique_name('roundtrip')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            ch.write(b'hello world')
            self.assertEqual(ch.read(), b'hello world')
        finally:
            ch.close()
            ch.unlink()

    def test_second_write_replaces_the_first(self):
        name = _unique_name('replace')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            ch.write(b'first')
            ch.write(b'second, and longer')
            self.assertEqual(ch.read(), b'second, and longer')
        finally:
            ch.close()
            ch.unlink()

    def test_write_over_capacity_raises(self):
        name = _unique_name('overflow')
        ch = ShmemChannel(name, capacity_bytes=4, create=True)
        try:
            with self.assertRaises(ValueError):
                ch.write(b'this is way too long')
        finally:
            ch.close()
            ch.unlink()

    def test_a_second_attachment_sees_the_same_data(self):
        name = _unique_name('attach')
        writer = ShmemChannel(name, capacity_bytes=64, create=True)
        writer.write(b'shared data')
        reader = ShmemChannel(name, capacity_bytes=64, create=False)
        try:
            self.assertEqual(reader.read(), b'shared data')
        finally:
            reader.close()
            writer.close()
            writer.unlink()

    def test_attaching_to_a_nonexistent_channel_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            ShmemChannel(_unique_name('never_created'), capacity_bytes=64, create=False)

    def test_mismatched_capacity_on_attach_raises(self):
        name = _unique_name('mismatch')
        writer = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            with self.assertRaises(ValueError):
                ShmemChannel(name, capacity_bytes=999999, create=False)
        finally:
            writer.close()
            writer.unlink()

    def test_label_defaults_to_empty_string(self):
        name = _unique_name('label_default')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            self.assertEqual(ch.label, '')
        finally:
            ch.close()
            ch.unlink()

    def test_label_is_stored_when_given(self):
        name = _unique_name('label_given')
        ch = ShmemChannel(name, capacity_bytes=64, create=True, label='video')
        try:
            self.assertEqual(ch.label, 'video')
        finally:
            ch.close()
            ch.unlink()

    def test_close_and_unlink_do_not_raise(self):
        # Regression check: numpy/memoryview views into the buffer
        # must be released before the underlying SharedMemory can
        # close, or this raises BufferError.
        name = _unique_name('clean_close')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        ch.write(b'data')
        ch.read()
        ch.close()  # must not raise
        ch2 = ShmemChannel(name, capacity_bytes=64, create=False)
        ch2.close()
        # Re-attach to unlink from the original owner's handle
        ch3 = ShmemChannel(name, capacity_bytes=64, create=False)
        ch3.unlink()  # must not raise
        ch3.close()


def _stress_writer(name: str, duration_s: float):
    ch = ShmemChannel(name, capacity_bytes=64, create=False)
    i = 0
    t_end = time.time() + duration_s
    while time.time() < t_end:
        # counter + a value derived from it -- if a read ever tears,
        # the two halves won't agree with each other.
        payload = struct.pack('<Q', i) + struct.pack('<Q', i * 7 + 3)
        ch.write(payload)
        i += 1
    ch.close()


class TestShmemChannelConcurrency(unittest.TestCase):
    """The property that actually matters: no torn reads, ever, under
    real concurrent multi-process access -- not just single-threaded
    correctness."""

    def test_no_torn_reads_under_real_concurrent_stress(self):
        name = _unique_name('stress')
        duration_s = 1.0
        creator = ShmemChannel(name, capacity_bytes=64, create=True)
        creator.write(struct.pack('<Q', 0) + struct.pack('<Q', 3))

        writer_proc = mp.Process(target=_stress_writer, args=(name, duration_s))
        writer_proc.start()

        reads = torn = non_monotonic = 0
        last_counter = -1
        t_end = time.time() + duration_s + 0.5
        try:
            while time.time() < t_end:
                raw = creator.read()
                if raw is None:
                    continue
                counter, checksum = struct.unpack('<QQ', raw)
                reads += 1
                if checksum != counter * 7 + 3:
                    torn += 1
                if counter < last_counter:
                    non_monotonic += 1
                last_counter = counter
        finally:
            writer_proc.join(timeout=5)
            creator.close()
            creator.unlink()

        self.assertGreater(reads, 100, 'suspiciously few reads -- something else may be wrong')
        self.assertEqual(torn, 0, f'{torn} torn reads out of {reads} -- the seqlock failed to prevent tearing')
        self.assertEqual(non_monotonic, 0, f'{non_monotonic} non-monotonic reads -- saw a value older than one already seen')


if __name__ == '__main__':
    unittest.main()


class TestWriteTrackingForHealth(unittest.TestCase):
    """write_count/seconds_since_write are the basis for "is this
    channel actually running as expected" -- staleness and rate."""

    def test_write_count_starts_at_zero(self):
        name = _unique_name('count0')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            self.assertEqual(ch.write_count(), 0)
        finally:
            ch.close(); ch.unlink()

    def test_write_count_increments_once_per_write(self):
        name = _unique_name('count_inc')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            ch.write(b'a')
            ch.write(b'b')
            ch.write(b'c')
            self.assertEqual(ch.write_count(), 3)
        finally:
            ch.close(); ch.unlink()

    def test_seconds_since_write_is_none_before_any_write(self):
        name = _unique_name('never_written')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            self.assertIsNone(ch.seconds_since_write())
        finally:
            ch.close(); ch.unlink()

    def test_seconds_since_write_is_small_right_after_a_write(self):
        name = _unique_name('just_written')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        try:
            ch.write(b'data')
            self.assertLess(ch.seconds_since_write(), 1.0)
            self.assertGreaterEqual(ch.seconds_since_write(), 0.0)
        finally:
            ch.close(); ch.unlink()

    def test_a_second_attachment_sees_the_same_write_tracking(self):
        name = _unique_name('shared_tracking')
        writer = ShmemChannel(name, capacity_bytes=64, create=True)
        writer.write(b'data')
        reader = ShmemChannel(name, capacity_bytes=64, create=False)
        try:
            self.assertEqual(reader.write_count(), 1)
            self.assertLess(reader.seconds_since_write(), 1.0)
        finally:
            reader.close(); writer.close(); writer.unlink()


class TestCloseIsIdempotent(unittest.TestCase):

    def test_calling_close_twice_does_not_raise(self):
        name = _unique_name('double_close')
        ch = ShmemChannel(name, capacity_bytes=64, create=True)
        ch.write(b'data')
        ch.close()
        ch.close()  # must not raise
        ch2 = ShmemChannel(name, capacity_bytes=64, create=False)
        ch2.unlink()
        ch2.close()
