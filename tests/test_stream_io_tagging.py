"""
tests/test_stream_io_tagging.py

Tests that build_desktop_capabilities now reports a speaker (audio
output) stream, not just screen/mic (both inputs from the endpoint's
perspective) -- streams represent both directions, not just input.
Also tests _fmt_stream's io tag display.
"""

import unittest
from unittest.mock import patch, MagicMock

from robonet.brain.util.selection_menu import _fmt_stream


class TestFmtStreamIoTag(unittest.TestCase):

    def test_video_stream_shows_io_tag(self):
        s = {'name': 'screen', 'type': 'video', 'io': 'I', 'width': 1920, 'height': 1080}
        self.assertIn('[I]', _fmt_stream(s))

    def test_audio_stream_shows_io_tag(self):
        s = {'name': 'speaker', 'type': 'audio', 'io': 'O', 'sample_rate': 48000, 'channels': 1}
        self.assertIn('[O]', _fmt_stream(s))

    def test_missing_io_falls_back_to_question_mark(self):
        s = {'name': 'mystery', 'type': 'audio', 'sample_rate': 48000, 'channels': 1}
        self.assertIn('[?]', _fmt_stream(s))

    def test_generic_stream_type_shows_io_tag_too(self):
        s = {'name': 'thing', 'type': 'other', 'io': 'I'}
        self.assertIn('[I]', _fmt_stream(s))


class TestBuildDesktopCapabilitiesStreams(unittest.TestCase):

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_includes_a_speaker_stream(self, mock_pyautogui):
        from robonet.endpoint.desktop_hardware import build_desktop_capabilities
        mock_pyautogui.size.return_value = MagicMock(width=1920, height=1080)

        caps = build_desktop_capabilities()

        names = [s['name'] for s in caps.streams()]
        self.assertIn('speaker', names)

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_speaker_is_tagged_as_output(self, mock_pyautogui):
        from robonet.endpoint.desktop_hardware import build_desktop_capabilities
        mock_pyautogui.size.return_value = MagicMock(width=1920, height=1080)

        caps = build_desktop_capabilities()

        speaker = next(s for s in caps.streams() if s['name'] == 'speaker')
        self.assertEqual(speaker['io'], 'O')

    @patch('robonet.endpoint.desktop_hardware.pyautogui')
    def test_screen_and_mic_are_tagged_as_input(self, mock_pyautogui):
        from robonet.endpoint.desktop_hardware import build_desktop_capabilities
        mock_pyautogui.size.return_value = MagicMock(width=1920, height=1080)

        caps = build_desktop_capabilities()

        by_name = {s['name']: s for s in caps.streams()}
        self.assertEqual(by_name['screen']['io'], 'I')
        self.assertEqual(by_name['mic']['io'], 'I')


if __name__ == '__main__':
    unittest.main()
