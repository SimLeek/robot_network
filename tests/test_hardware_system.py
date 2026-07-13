"""
tests/test_hardware_system.py

Tests robonet/endpoint/hardware_system.py's MultiAVRobotHardware: the
general N-video/N-audio-input/N-audio-output alternative to
CamMicSpkRobotHardware's fixed one-of-each. Covers source announcement,
selection (success, unknown id, switch failure with fallback), and the
SelectAVSource wire handler that ties selection to the network.

Constructs real instances against temp psk files (same pattern as
DesktopHw's own tests) rather than a lightweight fake, since the
interesting behavior here (fallback on failure, handler dispatch) spans
several cooperating pieces of real state.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import numpy as np

from robonet.buffers.buffer_objects import SelectAVSource, AVSourceError


class _ConcreteMultiAV:
    """Minimal concrete subclass -- MultiAVRobotHardware itself is
    abstract (apply_tensor/build_capabilities/halt are left for real
    subclasses like DesktopHw to implement); this just satisfies the ABC
    so tests can construct real instances and exercise the shared
    machinery directly."""
    def apply_tensor(self, idx_vec, val_vec):
        pass

    def build_capabilities(self):
        return None

    def halt(self):
        pass


class TestMultiAVRobotHardware(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='robonet_multiav_test_')
        self.psk_path = os.path.join(self.tmpdir, 'psk.key')
        with open(self.psk_path, 'wb') as f:
            f.write(os.urandom(32))
        import robonet.endpoint.settings as settings_
        self._settings = settings_.get()
        self._orig_psk = self._settings['psk_file']
        self._settings['psk_file'] = self.psk_path

    def tearDown(self):
        self._settings['psk_file'] = self._orig_psk
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _construct(self, **kwargs):
        from robonet.endpoint.hardware_system import MultiAVRobotHardware

        class _TestHw(_ConcreteMultiAV, MultiAVRobotHardware):
            pass

        defaults = dict(
            video_sources={'a': '/dev/video0', 'b': '/dev/video1'},
            audio_inputs={'mic': 'hw:0,0'},
            audio_outputs={'spk': 'hw:1,0'},
        )
        defaults.update(kwargs)
        return _TestHw(**defaults)

    def test_defaults_to_first_entry_of_each_dict(self):
        hw = self._construct()
        self.assertEqual(hw._active_video, 'a')
        self.assertEqual(hw._active_audio_in, 'mic')
        self.assertEqual(hw._active_audio_out, 'spk')

    def test_explicit_active_overrides_default(self):
        hw = self._construct(active_video='b')
        self.assertEqual(hw._active_video, 'b')

    def test_empty_dicts_do_not_crash_construction(self):
        hw = self._construct(video_sources={}, audio_inputs={}, audio_outputs={})
        self.assertIsNone(hw._active_video)
        self.assertIsNone(hw._active_audio_in)
        self.assertIsNone(hw._active_audio_out)

    def test_announce_sources_reflects_current_state(self):
        hw = self._construct()
        announce = hw.announce_sources()
        self.assertEqual(set(announce.video_ids), {'a', 'b'})
        self.assertEqual(announce.audio_in_ids, ['mic'])
        self.assertEqual(announce.audio_out_ids, ['spk'])
        self.assertEqual(announce.active_video_id, 'a')

    def test_select_video_source_switches_device(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()

        hw.select_video_source('b')

        hw._gst_sender.set_source_device.assert_called_once_with('/dev/video1')
        self.assertEqual(hw._active_video, 'b')

    def test_select_video_source_same_id_is_a_noop(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()

        hw.select_video_source('a')

        hw._gst_sender.set_source_device.assert_not_called()

    def test_select_video_source_unknown_id_raises_keyerror(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()

        with self.assertRaises(KeyError):
            hw.select_video_source('nonexistent')
        self.assertEqual(hw._active_video, 'a')  # unchanged

    def test_select_video_source_falls_back_on_switch_failure(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()
        hw._gst_sender.set_source_device.side_effect = [RuntimeError('device gone'), None]

        with self.assertRaises(RuntimeError):
            hw.select_video_source('b')

        # First call was the failed attempt at 'b'; second call (the
        # fallback) re-applies the previous device, 'a'.
        calls = hw._gst_sender.set_source_device.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].args[0], '/dev/video0')
        self.assertEqual(hw._active_video, 'a')  # never actually changed

    def test_select_audio_input_switches_device(self):
        hw = self._construct(audio_inputs={'mic': 'hw:0,0', 'loopback': 'hw:2,0'})
        hw._gst_sender = MagicMock()

        hw.select_audio_input('loopback')

        hw._gst_sender.set_mic_device.assert_called_once_with('hw:2,0')
        self.assertEqual(hw._active_audio_in, 'loopback')

    def test_select_audio_output_switches_via_direct_audio(self):
        hw = self._construct(audio_outputs={'spk': 'hw:1,0', 'hdmi': 'hw:3,0'})
        hw._gst_receiver = MagicMock()

        hw.select_audio_output('hdmi')

        hw._gst_receiver.set_direct_audio.assert_called_once_with(True, 'hw:3,0')
        self.assertEqual(hw._active_audio_out, 'hdmi')

    def test_on_select_source_dispatches_to_select_video_source(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()
        hw.root = MagicMock()

        hw._on_select_source('brain-host', SelectAVSource(kind='video', source_id='b'))

        hw._gst_sender.set_source_device.assert_called_once_with('/dev/video1')
        hw.root.radio.burst.assert_not_called()  # success -- no error report

    def test_on_select_source_unknown_kind_reports_error(self):
        hw = self._construct()
        hw.root = MagicMock()

        hw._on_select_source('brain-host', SelectAVSource(kind='smell', source_id='x'))

        hw.root.radio.burst.assert_called_once()
        sent = hw.root.radio.burst.call_args[0][0]
        self.assertIsInstance(sent, AVSourceError)
        self.assertEqual(sent.kind, 'smell')

    def test_on_select_source_unknown_id_reports_error_with_fallback(self):
        hw = self._construct()
        hw.root = MagicMock()

        hw._on_select_source('brain-host', SelectAVSource(kind='video', source_id='nonexistent'))

        hw.root.radio.burst.assert_called_once()
        sent = hw.root.radio.burst.call_args[0][0]
        self.assertIsInstance(sent, AVSourceError)
        self.assertEqual(sent.kind, 'video')
        self.assertEqual(sent.reverted_to, 'a')  # still on the original active id

    def test_on_select_source_switch_exception_reports_error(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()
        hw._gst_sender.set_source_device.side_effect = RuntimeError('device gone')
        hw.root = MagicMock()

        hw._on_select_source('brain-host', SelectAVSource(kind='video', source_id='b'))

        hw.root.radio.burst.assert_called_once()
        sent = hw.root.radio.burst.call_args[0][0]
        self.assertIsInstance(sent, AVSourceError)
        self.assertIn('device gone', sent.message)

    def test_handlers_include_select_av_source(self):
        hw = self._construct()
        self.assertIn('SelectAVSource', hw.handlers)

    def test_update_server_ip_announces_sources(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()
        hw.root = MagicMock()

        hw.update_server_ip('10.0.0.5')

        hw.root.radio.burst.assert_called_once()
        sent = hw.root.radio.burst.call_args[0][0]
        from robonet.buffers.buffer_objects import AVSourcesAnnounce
        self.assertIsInstance(sent, AVSourcesAnnounce)

    def test_apply_tensor_is_abstract_but_lifecycle_methods_work(self):
        hw = self._construct()
        hw._gst_sender = MagicMock()
        hw._gst_receiver = MagicMock()

        hw.start()
        hw._gst_sender.start.assert_called_once()
        hw._gst_receiver.start.assert_called_once()

        hw.stop()
        hw._gst_sender.stop.assert_called_once()
        hw._gst_receiver.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
