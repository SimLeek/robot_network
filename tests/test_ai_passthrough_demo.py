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
from robonet.gst_io.streamer_unencrypted import AUDIO_SOURCE_SINE_TEST


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

    def test_switches_to_sine_test_source(self):
        demo = AiPassthroughDemo()
        demo._root = MagicMock()
        demo._root.menu.gst_sender._mic_device = 'hw:1,0,0'

        _run(demo._play_sine_tone(seconds=0.0))

        calls = [c.args[0] for c in demo._root.menu.gst_sender.set_mic_device.call_args_list]
        self.assertEqual(calls[0], AUDIO_SOURCE_SINE_TEST)

    def test_restores_the_original_mic_device_afterward(self):
        demo = AiPassthroughDemo()
        demo._root = MagicMock()
        demo._root.menu.gst_sender._mic_device = 'hw:1,0,0'

        _run(demo._play_sine_tone(seconds=0.0))

        calls = [c.args[0] for c in demo._root.menu.gst_sender.set_mic_device.call_args_list]
        self.assertEqual(calls[-1], 'hw:1,0,0')


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


class TestFullRunSetsAndRestoresInputSource(unittest.TestCase):

    def test_sets_ai_then_restores_human_even_if_a_step_raises(self):
        from robonet.brain.desktop_system import DesktopSubSystem
        demo = AiPassthroughDemo(period_s=0.0)
        demo._root = MagicMock()
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
