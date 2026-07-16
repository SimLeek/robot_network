"""
tests/test_streamer_desktop_sources.py

Tests the direct-capture additions to robonet/gst_io/streamer_unencrypted.py:
  - _VideoPipeline: VIDEO_SOURCE_XIMAGESRC captures the desktop directly
    via ximagesrc instead of v4l2src reading a device path.
  - _AudioPipeline: AUDIO_SOURCE_DESKTOP_MIX mixes the desktop's system
    audio (PulseAudio monitor) and mic directly via two pulsesrc
    branches into an audiomixer, instead of a single alsasrc reading a
    loopback device.

Both replace what used to be a v4l2loopback/ALSA-loopback round trip
that produced blank output in practice -- these feed straight into the
same encode/send chain used for any real camera/mic.

Gst.ElementFactory.make is mocked throughout: what's under test is which
elements get created/linked/configured, not whether specific GStreamer
plugins happen to be installed on whatever machine runs the tests.
"""

import unittest
from unittest.mock import patch, MagicMock

from robonet.gst_io.streamer_unencrypted import (
    _VideoPipeline, _AudioPipeline, VIDEO_SOURCE_XIMAGESRC, AUDIO_SOURCE_DESKTOP_MIX, AUDIO_SOURCE_SINE_TEST,
)


def _make_factory_mock():
    """Gst.ElementFactory.make side_effect: returns a fresh MagicMock per
    call, tagged with which factory name created it so assertions can
    find "the pulsesrc for the mic branch" etc without relying on call
    order."""
    created = {}

    def make(factory_name, elem_name):
        mock = MagicMock(name=f'{factory_name}:{elem_name}')
        created.setdefault(factory_name, []).append((elem_name, mock))
        return mock

    return make, created


class TestVideoPipelineXimagesrc(unittest.TestCase):

    def _make(self, src_device):
        return _VideoPipeline(src_device=src_device, enc_name='x264enc', video_codec='h264',
                              receiver_ip='10.0.0.5', width=640, height=480, fps=30, bitrate=2_000_000)

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_desktop_source_creates_ximagesrc_not_v4l2src(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make(VIDEO_SOURCE_XIMAGESRC).build()

        self.assertTrue(ok)
        self.assertIn('ximagesrc', created)
        self.assertNotIn('v4l2src', created)

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_desktop_source_sets_use_damage_not_device(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        self._make(VIDEO_SOURCE_XIMAGESRC).build()

        _, ximagesrc_elem = created['ximagesrc'][0]
        ximagesrc_elem.set_property.assert_any_call('use-damage', False)
        for call in ximagesrc_elem.set_property.call_args_list:
            self.assertNotEqual(call.args[0], 'device')

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_real_device_path_still_uses_v4l2src(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make('/dev/video0').build()

        self.assertTrue(ok)
        self.assertIn('v4l2src', created)
        self.assertNotIn('ximagesrc', created)
        _, v4l2_elem = created['v4l2src'][0]
        v4l2_elem.set_property.assert_any_call('device', '/dev/video0')


class TestAudioPipelineDesktopMix(unittest.TestCase):

    def _make(self, mic_device):
        return _AudioPipeline(mic_device=mic_device, enc_name='opusenc', audio_codec='opus',
                              server_ip='10.0.0.5', sample_rate=48000)

    @patch('robonet.gst_io.streamer_unencrypted._pactl_get_default')
    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_mixes_monitor_and_distinct_mic(self, mock_make, _pipeline_new, mock_pactl):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn
        mock_pactl.side_effect = lambda field: {'sink': 'builtin-speaker', 'source': 'builtin-mic'}[field]

        ok = self._make(AUDIO_SOURCE_DESKTOP_MIX).build()

        self.assertTrue(ok)
        self.assertIn('audiomixer', created)
        self.assertEqual(len(created.get('pulsesrc', [])), 2)  # monitor branch + mic branch
        device_values = [call.args[1] for _, elem in created['pulsesrc']
                        for call in elem.set_property.call_args_list if call.args[0] == 'device']
        self.assertIn('builtin-speaker.monitor', device_values)
        self.assertIn('builtin-mic', device_values)
        self.assertNotIn('alsasrc', created)

    @patch('robonet.gst_io.streamer_unencrypted._pactl_get_default')
    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_skips_mic_when_it_equals_monitor(self, mock_make, _pipeline_new, mock_pactl):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn
        # source happens to report the same name as the monitor -- avoid double-counting it
        mock_pactl.side_effect = lambda field: {'sink': 'x', 'source': 'x.monitor'}[field]

        ok = self._make(AUDIO_SOURCE_DESKTOP_MIX).build()

        self.assertTrue(ok)
        self.assertEqual(len(created.get('pulsesrc', [])), 1)

    @patch('robonet.gst_io.streamer_unencrypted._pactl_get_default', return_value=None)
    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_no_sink_or_source_found_fails_gracefully(self, mock_make, _pipeline_new, _pactl):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make(AUDIO_SOURCE_DESKTOP_MIX).build()

        self.assertFalse(ok)  # no crash, just reports failure so the caller can try the next encoder/etc

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_real_alsa_device_still_uses_alsasrc(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make('hw:1,0,0').build()

        self.assertTrue(ok)
        self.assertIn('alsasrc', created)
        self.assertNotIn('audiomixer', created)
        _, alsa_elem = created['alsasrc'][0]
        alsa_elem.set_property.assert_any_call('device', 'hw:1,0,0')

    @patch('robonet.gst_io.streamer_unencrypted.Gst.Pipeline.new', return_value=MagicMock())
    @patch('robonet.gst_io.streamer_unencrypted.Gst.ElementFactory.make')
    def test_sine_test_tone_uses_audiotestsrc(self, mock_make, _pipeline_new):
        make_fn, created = _make_factory_mock()
        mock_make.side_effect = make_fn

        ok = self._make(AUDIO_SOURCE_SINE_TEST).build()

        self.assertTrue(ok)
        self.assertIn('audiotestsrc', created)
        self.assertNotIn('alsasrc', created)
        self.assertNotIn('audiomixer', created)
        _, sine_elem = created['audiotestsrc'][0]
        sine_elem.set_property.assert_any_call('wave', 'sine')
        sine_elem.set_property.assert_any_call('freq', 440.0)


class TestPactlGetDefault(unittest.TestCase):

    @patch('robonet.gst_io.streamer_unencrypted.subprocess.run')
    def test_returns_stripped_stdout(self, mock_run):
        mock_run.return_value = MagicMock(stdout='some-sink-name\n')
        from robonet.gst_io.streamer_unencrypted import _pactl_get_default
        self.assertEqual(_pactl_get_default('sink'), 'some-sink-name')

    @patch('robonet.gst_io.streamer_unencrypted.subprocess.run', side_effect=Exception('pactl not found'))
    def test_returns_none_on_failure(self, _run):
        from robonet.gst_io.streamer_unencrypted import _pactl_get_default
        self.assertIsNone(_pactl_get_default('sink'))


if __name__ == '__main__':
    unittest.main()
