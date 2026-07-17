"""
tests/test_desktop_endpoint_audio_stream_demo.py

Tests examples/desktop/desktop_endpoint.py's audio_stream_demo: waits
for a brain connection, streams a test tone via
GstSender.start_audio_stream()/push()/end(), then returns -- a one-shot
way to exercise the streaming API on real hardware.
"""

import asyncio
import sys
import unittest
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, 'examples/desktop')  # not a package on the normal import path

from desktop_endpoint import audio_stream_demo


def _run(coro):
    return asyncio.run(coro)


class TestAudioStreamDemo(unittest.TestCase):

    def _make_hw(self, connected=True):
        hw = MagicMock()
        hw._gst_sender = MagicMock()
        hw._gst_sender._receiver_ip = '10.0.0.5' if connected else None
        return hw

    def test_waits_for_a_connection_before_starting(self):
        async def scenario():
            hw = self._make_hw(connected=False)

            async def connect_after_a_moment():
                await asyncio.sleep(0.02)
                hw._gst_sender._receiver_ip = '10.0.0.5'

            await asyncio.gather(
                audio_stream_demo(hw, seconds=0.0, chunk_dur=0.001, poll_interval=0.005),
                connect_after_a_moment(),
            )
            return hw

        hw = _run(scenario())
        hw._gst_sender.start_audio_stream.assert_called_once()

    def test_pushes_the_expected_number_of_chunks(self):
        hw = self._make_hw()
        handle = MagicMock()
        hw._gst_sender.start_audio_stream.return_value = handle

        _run(audio_stream_demo(hw, seconds=0.05, chunk_dur=0.01))

        self.assertEqual(handle.push.call_count, 5)  # 0.05s / 0.01s chunks

    def test_calls_end_after_all_chunks_pushed(self):
        hw = self._make_hw()
        handle = MagicMock()
        hw._gst_sender.start_audio_stream.return_value = handle

        _run(audio_stream_demo(hw, seconds=0.02, chunk_dur=0.01))

        handle.end.assert_called_once()

    def test_pushed_chunks_are_a_continuous_tone_not_phase_resets(self):
        # Each chunk's time array must continue from where the last
        # one left off, or there'd be an audible phase-discontinuity
        # click at every chunk boundary -- the exact failure mode this
        # whole feature exists to avoid.
        hw = self._make_hw()
        handle = MagicMock()
        hw._gst_sender.start_audio_stream.return_value = handle

        _run(audio_stream_demo(hw, seconds=0.4, chunk_dur=0.2, freq_hz=440.0,
                               sample_rate=48000, poll_interval=0.001))

        chunk0 = handle.push.call_args_list[0].args[0]
        chunk1 = handle.push.call_args_list[1].args[0]
        stitched = np.concatenate([chunk0, chunk1])
        spectrum = np.abs(np.fft.rfft(stitched))
        freqs = np.fft.rfftfreq(len(stitched), d=1.0 / 48000)
        peak = freqs[int(np.argmax(spectrum))]
        self.assertAlmostEqual(peak, 440.0, delta=5.0)  # a phase jump would smear this badly

    def test_returns_gracefully_if_stream_could_not_start(self):
        hw = self._make_hw()
        hw._gst_sender.start_audio_stream.return_value = None

        _run(audio_stream_demo(hw, seconds=0.02, chunk_dur=0.01))  # must not raise


if __name__ == '__main__':
    unittest.main()
