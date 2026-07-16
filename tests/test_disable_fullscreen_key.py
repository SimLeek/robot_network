"""
tests/test_disable_fullscreen_key.py

Tests DisplaySubSystem._disable_builtin_fullscreen_key(): moderngl_window's
own base Window class binds F11 to toggle fullscreen by default, before
the keypress ever reaches pass_through_cb. fullscreen_key=None is
moderngl_window's own documented way to disable this default binding.
"""

import unittest
from unittest.mock import MagicMock


def _stub_out_missing_displayarray_font_submodule():
    """displayarray.window.mglwindow imports displayarray.font.get_texture_atlas,
    which doesn't exist in every installed displayarray build. Stub it out
    since this file never touches font rendering at all."""
    import sys, types
    if 'displayarray.font.get_texture_atlas' in sys.modules:
        return
    try:
        import displayarray.font.get_texture_atlas  # noqa: F401
        return
    except ImportError:
        pass
    fake_font_pkg = types.ModuleType('displayarray.font')
    fake_atlas_mod = types.ModuleType('displayarray.font.get_texture_atlas')
    fake_atlas_mod.get_or_create_font_npz = lambda *a, **kw: None
    sys.modules['displayarray.font'] = fake_font_pkg
    sys.modules['displayarray.font.get_texture_atlas'] = fake_atlas_mod


_stub_out_missing_displayarray_font_submodule()

from robonet.brain.display_system import DisplaySubSystem


def _make_sub():
    sub = DisplaySubSystem.__new__(DisplaySubSystem)
    sub.displayer = MagicMock()
    sub._fullscreen_key_disabled = False
    sub._audio_stream = None
    return sub


class TestDisableBuiltinFullscreenKey(unittest.TestCase):

    def test_sets_fullscreen_key_to_none_once_the_window_is_reachable(self):
        sub = _make_sub()
        sub._disable_builtin_fullscreen_key()
        self.assertIsNone(sub.displayer.displayer.config.wnd.fullscreen_key)

    def test_marks_itself_done_after_succeeding(self):
        sub = _make_sub()
        sub._disable_builtin_fullscreen_key()
        self.assertTrue(sub._fullscreen_key_disabled)

    def test_does_not_reattempt_once_already_disabled(self):
        sub = _make_sub()
        sub._disable_builtin_fullscreen_key()
        sub.displayer.displayer.config.wnd.fullscreen_key = 'something-else'

        sub._disable_builtin_fullscreen_key()

        self.assertEqual(sub.displayer.displayer.config.wnd.fullscreen_key, 'something-else')

    def test_gracefully_retries_when_window_not_yet_constructed(self):
        sub = _make_sub()

        class _RaisesUntilReady:
            def __getattr__(self, name):
                raise AttributeError('window not up yet')

        sub.displayer = _RaisesUntilReady()

        sub._disable_builtin_fullscreen_key()  # must not raise

        self.assertFalse(sub._fullscreen_key_disabled)


if __name__ == '__main__':
    unittest.main()
