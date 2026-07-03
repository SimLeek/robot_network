"""
tests/test_desktop_capture.py

Tests robonet/endpoint/desktop_capture.py:
  - Device discovery (find_v4l2loopback_device, find_alsa_loopback_card_index,
    ensure_*, find_real_webcam) against a mocked filesystem -- no real
    v4l2loopback/snd-aloop devices needed.
  - DesktopVideoFeeder/DesktopAudioFeeder pipeline construction against a
    mocked Gst.parse_launch, asserting on the actual pipeline description
    string built rather than on whether specific GStreamer plugins happen
    to be installed on whatever machine runs the tests (a portability/
    environment concern, not a logic concern).
"""

import unittest
from unittest.mock import patch, mock_open, MagicMock

from robonet.endpoint import desktop_capture as dc


class TestFindV4l2loopbackDevice(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.glob.glob')
    def test_finds_matching_label(self, mock_glob):
        mock_glob.return_value = [
            '/sys/class/video4linux/video0/name',
            '/sys/class/video4linux/video42/name',
        ]
        contents = {
            '/sys/class/video4linux/video0/name': 'Integrated Webcam\n',
            '/sys/class/video4linux/video42/name': 'RobonetDesktopCam\n',
        }
        with patch('builtins.open', side_effect=lambda p, *a, **kw: mock_open(read_data=contents[p]).return_value):
            result = dc.find_v4l2loopback_device()
        self.assertEqual(result, '/dev/video42')

    @patch('robonet.endpoint.desktop_capture.glob.glob')
    def test_returns_none_when_not_found(self, mock_glob):
        mock_glob.return_value = ['/sys/class/video4linux/video0/name']
        with patch('builtins.open', mock_open(read_data='Integrated Webcam\n')):
            result = dc.find_v4l2loopback_device()
        self.assertIsNone(result)

    @patch('robonet.endpoint.desktop_capture.glob.glob')
    def test_unreadable_entry_is_skipped_not_fatal(self, mock_glob):
        mock_glob.return_value = [
            '/sys/class/video4linux/video0/name',
            '/sys/class/video4linux/video42/name',
        ]
        def fake_open(path, *a, **kw):
            if path.endswith('video0/name'):
                raise OSError('permission denied')
            return mock_open(read_data='RobonetDesktopCam\n').return_value
        with patch('builtins.open', side_effect=fake_open):
            result = dc.find_v4l2loopback_device()
        self.assertEqual(result, '/dev/video42')

    def test_custom_label_respected(self):
        with patch('robonet.endpoint.desktop_capture.glob.glob',
                  return_value=['/sys/class/video4linux/video7/name']), \
             patch('builtins.open', mock_open(read_data='SomeOtherCam\n')):
            self.assertEqual(dc.find_v4l2loopback_device(label='SomeOtherCam'), '/dev/video7')


class TestEnsureV4l2loopbackDevice(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.find_v4l2loopback_device')
    def test_returns_device_when_found(self, mock_find):
        mock_find.return_value = '/dev/video42'
        self.assertEqual(dc.ensure_v4l2loopback_device(), '/dev/video42')

    @patch('robonet.endpoint.desktop_capture.find_v4l2loopback_device')
    def test_raises_actionable_error_when_missing(self, mock_find):
        mock_find.return_value = None
        with self.assertRaises(dc.DesktopCaptureError) as ctx:
            dc.ensure_v4l2loopback_device()
        self.assertIn('setup_desktop_capture.sh', str(ctx.exception))


class TestFindAlsaLoopbackCardIndex(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.os.path.exists', return_value=True)
    def test_finds_loopback_card(self, _exists):
        cards = ' 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n' \
               ' 1 [Loopback       ]: Loopback - Loopback\n'
        with patch('builtins.open', mock_open(read_data=cards)):
            self.assertEqual(dc.find_alsa_loopback_card_index(), 1)

    @patch('robonet.endpoint.desktop_capture.os.path.exists', return_value=True)
    def test_returns_none_when_no_loopback_card(self, _exists):
        cards = ' 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n'
        with patch('builtins.open', mock_open(read_data=cards)):
            self.assertIsNone(dc.find_alsa_loopback_card_index())

    @patch('robonet.endpoint.desktop_capture.os.path.exists', return_value=False)
    def test_returns_none_when_proc_asound_missing(self, _exists):
        self.assertIsNone(dc.find_alsa_loopback_card_index())


class TestEnsureAlsaLoopback(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.find_alsa_loopback_card_index', return_value=1)
    def test_returns_correct_playback_and_capture_pair(self, _idx):
        playback, capture = dc.ensure_alsa_loopback()
        self.assertEqual(playback, 'hw:1,0,0')
        self.assertEqual(capture, 'hw:1,1,0')

    @patch('robonet.endpoint.desktop_capture.find_alsa_loopback_card_index', return_value=None)
    def test_raises_actionable_error_when_missing(self, _idx):
        with self.assertRaises(dc.DesktopCaptureError) as ctx:
            dc.ensure_alsa_loopback()
        self.assertIn('setup_desktop_capture.sh', str(ctx.exception))


class TestFindRealWebcam(unittest.TestCase):

    @patch('robonet.gst_io.devices.find_camera_devices')
    def test_excludes_own_loopback_device(self, mock_find):
        mock_find.return_value = ['/dev/video42', '/dev/video0']
        self.assertEqual(dc.find_real_webcam(exclude_device='/dev/video42'), '/dev/video0')

    @patch('robonet.gst_io.devices.find_camera_devices')
    def test_returns_none_when_only_loopback_present(self, mock_find):
        mock_find.return_value = ['/dev/video42']
        self.assertIsNone(dc.find_real_webcam(exclude_device='/dev/video42'))

    @patch('robonet.gst_io.devices.find_camera_devices')
    def test_returns_none_when_no_cameras_at_all(self, mock_find):
        mock_find.return_value = []
        self.assertIsNone(dc.find_real_webcam(exclude_device='/dev/video42'))


class TestDesktopVideoFeederBuild(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_pipeline_string_uses_configured_device_and_resolution(self, mock_parse):
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopVideoFeeder('/dev/video42', width=1280, height=720, fps=15)

        ok = feeder.build()

        self.assertTrue(ok)
        desc = mock_parse.call_args[0][0]
        self.assertIn('device=/dev/video42', desc)
        self.assertIn('width=1280', desc)
        self.assertIn('height=720', desc)
        self.assertIn('framerate=15/1', desc)
        self.assertIn('ximagesrc', desc)
        self.assertIn('v4l2sink', desc)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_build_failure_returns_false_not_raise(self, mock_parse):
        mock_parse.side_effect = dc.GLib.Error('bad pipeline')
        feeder = dc.DesktopVideoFeeder('/dev/video42')

        self.assertFalse(feeder.build())

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_start_builds_if_not_already_built(self, mock_parse):
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.SUCCESS
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopVideoFeeder('/dev/video42')

        with patch('robonet.endpoint.desktop_capture.GLib.MainLoop') as mock_loop_cls, \
             patch('robonet.endpoint.desktop_capture.threading.Thread') as mock_thread_cls:
            mock_loop_cls.return_value = MagicMock()
            mock_thread_cls.return_value = MagicMock()
            ok = feeder.start()

        self.assertTrue(ok)
        mock_parse.assert_called_once()
        fake_pipeline.set_state.assert_called_with(dc.Gst.State.PLAYING)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    def test_start_returns_false_when_state_change_fails(self, mock_parse):
        fake_pipeline = MagicMock()
        fake_pipeline.set_state.return_value = dc.Gst.StateChangeReturn.FAILURE
        mock_parse.return_value = fake_pipeline
        feeder = dc.DesktopVideoFeeder('/dev/video42')

        self.assertFalse(feeder.start())

    def test_stop_before_start_does_not_raise(self):
        feeder = dc.DesktopVideoFeeder('/dev/video42')
        feeder.stop()  # should be a no-op, not an AttributeError


class TestDesktopAudioFeederBuild(unittest.TestCase):

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_mixes_monitor_and_distinct_mic(self, mock_pactl, mock_parse):
        mock_pactl.side_effect = lambda field: 'alsa_output.pci-0000.sink' if field == 'sink' else 'alsa_input.usb-mic'
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopAudioFeeder('hw:1,0,0', include_mic=True)

        ok = feeder.build()

        self.assertTrue(ok)
        desc = mock_parse.call_args[0][0]
        self.assertIn('alsa_output.pci-0000.sink.monitor', desc)
        self.assertIn('alsa_input.usb-mic', desc)
        self.assertIn('alsasink device=hw:1,0,0', desc)
        # two distinct pulsesrc branches feeding the mixer
        self.assertEqual(desc.count('pulsesrc'), 2)

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_skips_mic_when_it_equals_monitor(self, mock_pactl, mock_parse):
        # e.g. default source somehow resolves to the same device as the monitor
        mock_pactl.side_effect = lambda field: 'same_device' if field == 'sink' else 'same_device.monitor'
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopAudioFeeder('hw:1,0,0', include_mic=True)

        feeder.build()

        desc = mock_parse.call_args[0][0]
        self.assertEqual(desc.count('pulsesrc'), 1)

    @patch.object(dc.DesktopAudioFeeder, '_pactl_get', return_value=None)
    def test_no_sink_or_source_disables_gracefully_no_raise(self, _pactl):
        feeder = dc.DesktopAudioFeeder('hw:1,0,0')

        ok = feeder.build()

        self.assertFalse(ok)  # degrades gracefully, does not raise

    @patch('robonet.endpoint.desktop_capture.Gst.parse_launch')
    @patch.object(dc.DesktopAudioFeeder, '_pactl_get')
    def test_include_mic_false_only_uses_monitor(self, mock_pactl, mock_parse):
        mock_pactl.return_value = 'some_device'
        mock_parse.return_value = MagicMock()
        feeder = dc.DesktopAudioFeeder('hw:1,0,0', include_mic=False)

        feeder.build()

        desc = mock_parse.call_args[0][0]
        self.assertEqual(desc.count('pulsesrc'), 1)


class _FakeGstSenderForSourceSwitch:
    """Minimal GstSender stand-in for set_source_device -- just the
    attributes that method actually touches."""
    def __init__(self, src_device, acked=True, receiver_ip='10.0.0.1', has_candidates=True):
        self._src_device = src_device
        self._acked = acked
        self._receiver_ip = receiver_ip
        self._video_candidates = ['h264'] if has_candidates else []
        self._vpipe = None
        self.started_pipelines = []

    def _start_video_pipeline(self):
        pipe = MagicMock(name=f'pipeline-for-{self._src_device}')
        self.started_pipelines.append((self._src_device, pipe))
        return pipe


class TestGstSenderSetSourceDevice(unittest.TestCase):
    """robonet/gst_io/streamer_unencrypted.py's GstSender.set_source_device,
    the mechanism behind the desktop-mode camera/desktop-capture switch."""

    def _bind(self, fake):
        from robonet.gst_io.streamer_unencrypted import GstSender
        return GstSender.set_source_device.__get__(fake)

    def test_same_device_is_a_no_op(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42')
        set_source_device = self._bind(fake)

        set_source_device('/dev/video42')

        self.assertEqual(fake.started_pipelines, [])

    def test_not_yet_streaming_just_stores_device_no_pipeline_restart(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=False)
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        self.assertEqual(fake._src_device, '/dev/video0')
        self.assertEqual(fake.started_pipelines, [])

    def test_streaming_switches_device_and_restarts_pipeline(self):
        old_pipe = MagicMock()
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=True)
        fake._vpipe = old_pipe
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        old_pipe.stop.assert_called_once()
        self.assertEqual(fake._src_device, '/dev/video0')
        self.assertEqual(len(fake.started_pipelines), 1)
        self.assertEqual(fake.started_pipelines[0][0], '/dev/video0')
        self.assertIsNotNone(fake._vpipe)

    def test_streaming_but_no_prior_pipeline_still_starts_new_one(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=True)
        fake._vpipe = None  # e.g. video encoder never successfully started before
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        self.assertEqual(len(fake.started_pipelines), 1)

    def test_no_receiver_ip_does_not_attempt_pipeline_start(self):
        fake = _FakeGstSenderForSourceSwitch('/dev/video42', acked=True, receiver_ip=None)
        set_source_device = self._bind(fake)

        set_source_device('/dev/video0')

        self.assertEqual(fake.started_pipelines, [])
        self.assertEqual(fake._src_device, '/dev/video0')  # device is still updated though


if __name__ == '__main__':
    unittest.main()
