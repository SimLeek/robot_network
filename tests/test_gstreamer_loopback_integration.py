"""
tests/test_gstreamer_loopback_integration.py

No mocks at the pipeline level: sends a real numpy sine array through
the actual _AudioPipeline (streamer_unencrypted.py) and receives it
through the actual _AudioRecvPipeline (receiver_unencrypted.py), over
real UDP loopback (127.0.0.1). This is the thing unit tests with mocked
Gst.ElementFactory.make can't catch -- whether the whole chain (appsrc
feed -> encode -> RTP -> udpsink -> udpsrc -> depay -> decode -> S16LE
appsink) actually produces clean audio: no clicks, no pauses, no
corruption, at the right frequency.

Runs real GStreamer with audiotestsrc-class elements (appsrc/opusenc/
opusdec/udpsink/udpsrc/appsink) against no real hardware at all -- these
all work in a sandboxed container. Calibrated empirically before being
written: a 1-second, 440Hz, amplitude-0.5 sine round-tripped with 99.7%
of its spectral energy still concentrated within 40Hz of the target
frequency, zero NaN, and a received sample count within 1% of sent.
Thresholds below are deliberately looser than that measurement to avoid
flaking on a slower CI run, while still meaningfully catching a broken
chain.
"""

import time
import unittest
from unittest.mock import MagicMock

import numpy as np

from robonet.gst_io.streamer_unencrypted import _AudioPipeline
from robonet.gst_io.receiver_unencrypted import _AudioRecvPipeline


class TestAudioRoundTripsThroughRealGstreamer(unittest.TestCase):

    def test_sine_wave_survives_the_real_send_receive_chain(self):
        sample_rate = 48000
        freq_hz = 440.0
        duration_s = 1.0
        t = np.arange(0, duration_s, 1.0 / sample_rate)
        sent = (0.5 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)

        received_chunks = []
        recv_info = MagicMock(audio_codec='opus', audio_port=5601, sample_rate=sample_rate)
        recv_pipe = _AudioRecvPipeline(
            recv_info, 'opusdec', direct_audio=False, audio_device='',
            on_audio=received_chunks.append, play_locally=False)
        send_pipe = _AudioPipeline(
            mic_device='default', enc_name='opusenc', audio_codec='opus',
            server_ip='127.0.0.1', sample_rate=sample_rate, array_source=sent)

        try:
            self.assertTrue(recv_pipe.build(), 'receive pipeline failed to build')
            recv_pipe.play()
            self.assertTrue(send_pipe.build(), 'send pipeline failed to build')
            send_pipe.play()

            # Real-time playback (the feeder thread paces itself to
            # duration_s), plus generous margin for encode/decode/RTP/
            # poll-thread latency and pipeline startup.
            time.sleep(duration_s + 2.5)
        finally:
            send_pipe.stop()
            recv_pipe.stop()

        self.assertTrue(received_chunks, 'no audio was received at all')
        received = np.concatenate(received_chunks)

        # 1) Data actually arrived, roughly the right amount of it --
        # generous bounds around the empirically-measured ~1.00 ratio,
        # since real threads/network timing vary run to run.
        ratio = len(received) / len(sent)
        self.assertGreater(ratio, 0.6, f'lost too much audio: ratio={ratio:.2f}')
        self.assertLess(ratio, 1.6, f'suspiciously more data than sent: ratio={ratio:.2f}')

        # 2) No corruption (format mismatches upstream show up as NaN
        # or wildly out-of-range values here).
        self.assertFalse(np.isnan(received).any(), 'received audio contains NaN')
        self.assertLessEqual(np.abs(received).max(), 1.05, 'received audio out of [-1,1] range')

        # 3) Actually has signal, not silence (measured RMS ~0.345 for
        # amplitude 0.5; floor set well below that).
        rms = float(np.sqrt(np.mean(received.astype(np.float64) ** 2)))
        self.assertGreater(rms, 0.1, f'received audio is near-silent: rms={rms:.4f}')

        # 4) Right frequency -- the direct correctness check.
        spectrum = np.abs(np.fft.rfft(received))
        freqs = np.fft.rfftfreq(len(received), d=1.0 / sample_rate)
        peak_hz = freqs[int(np.argmax(spectrum))]
        self.assertAlmostEqual(peak_hz, freq_hz, delta=10.0)

        # 5) Clean, not glitchy -- clicks/pauses/corruption spread
        # energy across the spectrum instead of concentrating it at the
        # true frequency. This is the direct "no clicks or pauses"
        # check: measured 99.7% in the calibration run; 0.85 is a
        # generous floor that still catches a genuinely broken chain.
        band = (freqs > freq_hz - 40) & (freqs < freq_hz + 40)
        purity = float(np.sum(spectrum[band] ** 2) / np.sum(spectrum ** 2))
        self.assertGreater(purity, 0.85, f'spectral energy too spread out (clicks/corruption?): {purity:.3f}')


if __name__ == '__main__':
    unittest.main()
