"""
tests/test_ai_passthrough_demo.py

Tests examples/ai_passthrough_demo.py's AiPassthroughDemo helpers in
isolation: the circular-motion math, the right-click/F11 sequences,
sine-tone source restoration, and waiting for a desktop connection.
Doesn't exercise the real main()/asyncio.run() entry point -- that's
an actual process launcher, not something to unit test.
"""

import asyncio
import sys
import unittest

import numpy as np
from unittest.mock import MagicMock, AsyncMock, patch


def _stub_out_missing_displayarray_font_submodule():
    """displayarray.window.mglwindow imports displayarray.font.get_texture_atlas,
    which doesn't exist in every installed displayarray build (it's on an
    actively-developed branch). Stub it out since this file never touches
    font rendering at all -- this only exists to make the demo script's
    imports (which pull in display_system.py) work, not to test displayarray."""
    import types
    if 'displayarray.font.get_texture_atlas' in sys.modules:
        return
    try:
        import displayarray.font.get_texture_atlas  # noqa: F401
        return  # the real thing is present -- nothing to stub
    except ImportError:
        pass
    fake_font_pkg = types.ModuleType('displayarray.font')
    fake_atlas_mod = types.ModuleType('displayarray.font.get_texture_atlas')
    fake_atlas_mod.get_or_create_font_npz = lambda *a, **kw: None
    sys.modules['displayarray.font'] = fake_font_pkg
    sys.modules['displayarray.font.get_texture_atlas'] = fake_atlas_mod


_stub_out_missing_displayarray_font_submodule()

sys.path.insert(0, 'examples')  # examples/ isn't a package on the normal import path

from ai_passthrough_demo import AiPassthroughDemo
from robonet.brain.desktop_system import (
    AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y,
    AI_TOKEN_MOUSE_RIGHT_PRESS, AI_TOKEN_MOUSE_RIGHT_RELEASE,
)


def _run(coro):
    """Runs a coroutine synchronously, patching sleep to be instant so
    tests don't actually wait for the demo's real timing."""
    async def _inner():
        return await coro
    return asyncio.run(_inner())


class TestCircleMouse(unittest.TestCase):

    def _fast_demo(self):
        demo = AiPassthroughDemo(radius_frac=0.2, period_s=0.0)  # period=0 -- no real waiting
        return demo

    def test_drives_the_mouse_neurons(self):
        demo = self._fast_demo()
        af = MagicMock()

        _run(demo._circle_mouse(af, steps=8))

        self.assertEqual(af.on_neuron_outputs.call_count, 8)

    def test_first_step_starts_at_the_radius_offset_from_center(self):
        demo = self._fast_demo()
        af = MagicMock()

        _run(demo._circle_mouse(af, steps=4))

        first_vector = af.on_neuron_outputs.call_args_list[0].args[0]
        # t=0 -> cos(0)=1, sin(0)=0 -- offset fully in +x, none in y
        self.assertAlmostEqual(first_vector[AI_NEURON_MOUSE_X], 0.5 + 0.2)
        self.assertAlmostEqual(first_vector[AI_NEURON_MOUSE_Y], 0.5)

    def test_stays_within_valid_fraction_range(self):
        demo = self._fast_demo()
        af = MagicMock()

        _run(demo._circle_mouse(af, steps=50))

        for call in af.on_neuron_outputs.call_args_list:
            vector = call.args[0]
            self.assertGreaterEqual(vector[AI_NEURON_MOUSE_X], 0.0)
            self.assertLessEqual(vector[AI_NEURON_MOUSE_X], 1.0)
            self.assertGreaterEqual(vector[AI_NEURON_MOUSE_Y], 0.0)
            self.assertLessEqual(vector[AI_NEURON_MOUSE_Y], 1.0)

    def test_traces_a_genuine_circle_not_a_fixed_point(self):
        demo = self._fast_demo()
        af = MagicMock()

        _run(demo._circle_mouse(af, steps=4))

        positions = [(c.args[0][AI_NEURON_MOUSE_X], c.args[0][AI_NEURON_MOUSE_Y])
                    for c in af.on_neuron_outputs.call_args_list]
        self.assertEqual(len(set(positions)), 4)  # 4 distinct points, not all the same


class TestRightClick(unittest.TestCase):

    def test_presses_then_releases_in_order(self):
        demo = AiPassthroughDemo()
        af = MagicMock()

        _run(demo._right_click(af))

        calls = [c.args[0] for c in af.on_token.call_args_list]
        self.assertEqual(calls, [AI_TOKEN_MOUSE_RIGHT_PRESS, AI_TOKEN_MOUSE_RIGHT_RELEASE])


class TestTapF11(unittest.TestCase):

    def test_presses_then_releases_f11(self):
        demo = AiPassthroughDemo()
        desktop = MagicMock()

        _run(demo._tap_f11(desktop))

        desktop.ai_key_press.assert_called_once_with('f11')
        desktop.ai_key_release.assert_called_once_with('f11')


class TestPlaySineTone(unittest.TestCase):

    def test_sends_a_real_sine_array_through_play_array(self):
        demo = AiPassthroughDemo()
        demo._root = MagicMock()

        with patch('asyncio.sleep', new=AsyncMock()):
            _run(demo._play_sine_tone(seconds=0.05, freq_hz=440.0, sample_rate=48000))

        demo._root.menu.gst_sender.play_array.assert_called_once()
        array, rate = demo._root.menu.gst_sender.play_array.call_args[0]
        self.assertEqual(rate, 48000)
        self.assertEqual(len(array), int(0.05 * 48000))

    def test_array_is_a_genuine_sine_wave_at_the_requested_frequency(self):
        demo = AiPassthroughDemo()
        demo._root = MagicMock()

        with patch('asyncio.sleep', new=AsyncMock()):
            _run(demo._play_sine_tone(seconds=0.5, freq_hz=440.0, sample_rate=48000))

        array, rate = demo._root.menu.gst_sender.play_array.call_args[0]
        spectrum = np.abs(np.fft.rfft(array))
        freqs = np.fft.rfftfreq(len(array), d=1.0 / rate)
        peak = freqs[int(np.argmax(spectrum))]
        self.assertAlmostEqual(peak, 440.0, delta=5.0)

    def test_no_mic_device_bookkeeping_needed_by_the_caller(self):
        # play_array is self-contained now -- the old sine-test sentinel
        # required the caller to save/restore _mic_device via
        # set_mic_device; that dance is gone entirely.
        demo = AiPassthroughDemo()
        demo._root = MagicMock()

        with patch('asyncio.sleep', new=AsyncMock()):
            _run(demo._play_sine_tone(seconds=0.0))

        demo._root.menu.gst_sender.set_mic_device.assert_not_called()


class TestWaitForDesktopConnection(unittest.TestCase):

    def test_returns_immediately_if_already_connected(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        demo = AiPassthroughDemo()
        demo._root = MagicMock()
        demo._root.active_sub = MagicMock(spec=DesktopSubSystem)

        result = _run(demo._wait_for_desktop_connection())

        self.assertIs(result, demo._root.active_sub)

    def test_waits_until_a_desktop_subsystem_connects(self):
        from robonet.brain.desktop_system import DesktopSubSystem

        async def scenario():
            demo = AiPassthroughDemo()
            demo._root = MagicMock()
            demo._root.active_sub = None  # not connected yet

            async def connect_after_a_moment():
                await asyncio.sleep(0.02)
                demo._root.active_sub = MagicMock(spec=DesktopSubSystem)

            waiter = asyncio.ensure_future(demo._wait_for_desktop_connection(poll_interval=0.005))
            connector = asyncio.ensure_future(connect_after_a_moment())
            result, _ = await asyncio.gather(waiter, connector)
            return result, demo

        result, demo = asyncio.run(scenario())
        self.assertIs(result, demo._root.active_sub)


class TestWaitForMediaReady(unittest.TestCase):

    def test_returns_immediately_if_already_flowing(self):
        demo = AiPassthroughDemo()
        demo.has_video = True
        demo.has_audio = True
        _run(demo._wait_for_media_ready())  # must not hang

    def test_waits_until_both_video_and_audio_are_flowing(self):
        async def scenario():
            demo = AiPassthroughDemo()
            demo.has_video = False
            demo.has_audio = False

            async def media_arrives_after_a_moment():
                await asyncio.sleep(0.01)
                demo.has_video = True
                await asyncio.sleep(0.01)
                demo.has_audio = True

            waiter = asyncio.ensure_future(demo._wait_for_media_ready(poll_interval=0.005))
            feeder = asyncio.ensure_future(media_arrives_after_a_moment())
            await asyncio.gather(waiter, feeder)
            return demo

        demo = asyncio.run(scenario())
        self.assertTrue(demo.has_video)
        self.assertTrue(demo.has_audio)

    def test_does_not_return_with_only_video_and_no_audio(self):
        async def scenario():
            demo = AiPassthroughDemo()
            demo.has_video = True
            demo.has_audio = False

            waiter = asyncio.ensure_future(demo._wait_for_media_ready(poll_interval=0.005))
            done, pending = await asyncio.wait([waiter], timeout=0.05)
            for p in pending:
                p.cancel()
            return done, pending

        done, pending = asyncio.run(scenario())
        self.assertEqual(len(done), 0)
        self.assertEqual(len(pending), 1)


class TestFullRunSetsAndRestoresInputSource(unittest.TestCase):

    def test_sets_ai_then_restores_human_even_if_a_step_raises(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        demo = AiPassthroughDemo(period_s=0.0)
        demo._root = MagicMock()
        demo.has_video = True
        demo.has_audio = True
        desktop = MagicMock(spec=DesktopSubSystem)
        desktop.af_ai = MagicMock()
        demo._root.active_sub = desktop
        demo._circle_mouse = AsyncMock(side_effect=RuntimeError('boom'))

        with self.assertRaises(RuntimeError):
            _run(demo._run())

        desktop.set_input_source.assert_any_call('ai')
        desktop.set_input_source.assert_any_call('human')  # still restored despite the failure


if __name__ == '__main__':
    unittest.main()


class TestDiagnosticLoop(unittest.TestCase):
    """Continually reports two cheap, human-checkable liveness signals:
    center-pixel hue (in_img) and peak audio frequency (in_aud)."""

    def _one_tick(self, demo):
        """Runs exactly one full iteration of the infinite loop: sleep
        returns normally the first time (letting the logging code after
        it run), then raises on the second call to break out cleanly."""
        calls = []

        async def fake_sleep(_):
            calls.append(1)
            if len(calls) >= 2:
                raise asyncio.CancelledError()

        with patch('asyncio.sleep', side_effect=fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                _run(demo._diagnostic_loop(interval_s=0.0))

    def test_logs_center_pixel_hue_for_a_red_frame(self):
        demo = AiPassthroughDemo()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :] = (255, 0, 0)  # pure red -- hue 0 deg
        demo.in_img = frame
        demo.in_aud = None

        with patch('ai_passthrough_demo.log') as mock_log:
            self._one_tick(demo)

        logged = ' '.join(str(c.args[0]) for c in mock_log.info.call_args_list)
        self.assertIn('center pixel hue', logged)
        self.assertIn('0.0 deg', logged)

    def test_logs_peak_frequency_for_a_known_tone(self):
        demo = AiPassthroughDemo()
        demo.in_img = None
        sr = 48000
        t = np.arange(0, 0.1, 1.0 / sr)
        demo.in_aud = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)

        with patch('ai_passthrough_demo.log') as mock_log:
            self._one_tick(demo)

        logged = ' '.join(str(c.args[0]) for c in mock_log.info.call_args_list)
        self.assertIn('peak audio frequency', logged)
        self.assertIn('440.', logged)

    def test_no_image_or_audio_yet_does_not_crash(self):
        demo = AiPassthroughDemo()
        demo.in_img = None
        demo.in_aud = None

        self._one_tick(demo)  # must not raise (other than the intentional CancelledError)

    def test_is_included_in_async_loops_alongside_run(self):
        demo = AiPassthroughDemo()
        loops = demo.async_loops(MagicMock())
        self.assertEqual(len(loops), 2)
        for l in loops:
            l.close()  # avoid the un-awaited-coroutine warning -- not executing them here
