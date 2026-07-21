"""
tests/test_server_system.py

Regression test: ServerSystem.async_loops() called self.displayer.run(self)
unconditionally, which crashed immediately in a headless session
(displayer=None, ai=something) -- a combination the constructor's own
assertion (`assert displayer or ai`) explicitly allows.
"""

import unittest
from unittest.mock import MagicMock, patch


def _stub_out_missing_displayarray_font_submodule():
    """displayarray.window.mglwindow imports displayarray.font.get_texture_atlas,
    which doesn't exist in every installed displayarray build (it's on an
    actively-developed branch). Stub it out since this file never touches
    font rendering at all -- this only exists to make ServerSystem
    importable (it pulls in display_system.py), not to test displayarray."""
    import sys, types
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

from robonet.brain.main_system import ServerSystem


def _make_server(with_displayer=True, with_ai=False, with_bridge=False):
    radio = MagicMock()
    radio.async_loops.return_value = ['radio-loop']
    menu = MagicMock()
    menu.async_loops.return_value = ['menu-loop']
    displayer = None
    if with_displayer:
        displayer = MagicMock()
        displayer.run.return_value = 'displayer-loop'
    ai = MagicMock() if with_ai else None
    bridge = MagicMock() if with_bridge else None
    return ServerSystem(radio, menu, displayer=displayer, ai=ai, bridge=bridge)


class TestAsyncLoopsHeadless(unittest.TestCase):

    def test_headless_session_does_not_crash(self):
        server = _make_server(with_displayer=False, with_ai=True)
        server.async_loops()  # must not raise

    def test_headless_session_omits_a_displayer_loop(self):
        server = _make_server(with_displayer=False, with_ai=True)
        loops = server.async_loops()
        self.assertNotIn('displayer-loop', loops)

    def test_headless_session_still_includes_radio_and_menu_loops(self):
        server = _make_server(with_displayer=False, with_ai=True)
        loops = server.async_loops()
        self.assertIn('radio-loop', loops)
        self.assertIn('menu-loop', loops)


class TestAiSubsystemLifecycle(unittest.TestCase):
    """Regression tests: self.ai's start/stop/async_loops were never
    actually called anywhere -- an AI subsystem got setup() in the
    constructor, but nothing ran it, so its coroutines never got
    scheduled by asyncio.gather() at all."""

    def test_async_loops_includes_ai_subsystem_loops(self):
        server = _make_server(with_displayer=False, with_ai=True)
        server.ai.async_loops.return_value = ['ai-loop']

        loops = server.async_loops()

        self.assertIn('ai-loop', loops)

    def test_start_calls_ai_start(self):
        server = _make_server(with_displayer=False, with_ai=True)
        server.loop = MagicMock()  # start() calls asyncio.get_running_loop(), avoid needing a real one
        with patch('asyncio.get_running_loop'):
            server.start()
        server.ai.start.assert_called_once()

    def test_stop_calls_ai_stop(self):
        server = _make_server(with_displayer=False, with_ai=True)
        server.stop()
        server.ai.stop.assert_called_once()

    def test_no_ai_subsystem_does_not_crash_start_stop_or_async_loops(self):
        server = _make_server(with_displayer=True, with_ai=False)
        with patch('asyncio.get_running_loop'):
            server.start()  # must not raise
        server.async_loops()  # must not raise
        server.stop()  # must not raise


class TestBridgeLifecycle(unittest.TestCase):
    """Same shape as TestAiSubsystemLifecycle -- an attached bridge
    needs to actually get started/stopped, and a bridge-less
    ServerSystem must keep working exactly as before."""

    def test_start_calls_bridge_start(self):
        server = _make_server(with_displayer=False, with_ai=True, with_bridge=True)
        with patch('asyncio.get_running_loop'):
            server.start()
        server.bridge.start.assert_called_once()

    def test_stop_calls_bridge_stop(self):
        server = _make_server(with_displayer=False, with_ai=True, with_bridge=True)
        server.stop()
        server.bridge.stop.assert_called_once()

    def test_no_bridge_does_not_crash_start_stop_or_async_loops(self):
        server = _make_server(with_displayer=True, with_ai=False, with_bridge=False)
        with patch('asyncio.get_running_loop'):
            server.start()  # must not raise
        server.async_loops()  # must not raise
        server.stop()  # must not raise


class TestAsyncLoopsWithDisplayer(unittest.TestCase):

    def test_includes_the_displayer_loop(self):
        server = _make_server(with_displayer=True)
        loops = server.async_loops()
        self.assertIn('displayer-loop', loops)
        server.displayer.run.assert_called_once_with(server)

    def test_still_includes_radio_and_menu_loops(self):
        server = _make_server(with_displayer=True)
        loops = server.async_loops()
        self.assertIn('radio-loop', loops)
        self.assertIn('menu-loop', loops)


class TestAsyncLoopsActiveSub(unittest.TestCase):

    def test_includes_active_sub_loops_when_present(self):
        server = _make_server(with_displayer=True)
        server.active_sub = MagicMock()
        server.active_sub.async_loops.return_value = ['sub-loop']

        loops = server.async_loops()

        self.assertIn('sub-loop', loops)

    def test_omits_active_sub_loops_when_none(self):
        server = _make_server(with_displayer=True)
        self.assertIsNone(server.active_sub)

        loops = server.async_loops()  # must not raise

        self.assertNotIn('sub-loop', loops)


if __name__ == '__main__':
    unittest.main()
