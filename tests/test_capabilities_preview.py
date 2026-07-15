"""
tests/test_capabilities_preview.py

Tests the pre-connection capability preview: pressing right-arrow on a
discovered (not yet connected) endpoint in the radio menu shows its
capabilities without connecting, and returns to the radio menu (not
main_menu) on escape -- without disturbing whatever the actually-
connected endpoint's capabilities are, if any.
"""

import unittest
from unittest.mock import MagicMock, patch

from robonet.brain.util.selection_menu import SelectionMenu


def _make_menu_with_endpoint(ready=True):
    fake_ep = MagicMock(
        ip='10.0.0.5', hostname='desk1', endpoint_type='desktop',
        axes=[{'name': 'mouse_move_x'}], streams=[{'name': 'screen'}],
        capabilities_received=ready,
    )
    menu = SelectionMenu(width=320, height=240)
    menu.root = MagicMock()
    menu.root.radio.mode = 'ADHOC'
    menu.root.radio.NetMode = MagicMock()
    menu.root.radio.is_scanning = False
    menu.menu_state.send('radio')  # enter the radio menu, matching real navigation
    return menu, fake_ep


class TestPreviewFromRadioMenu(unittest.TestCase):

    def test_right_arrow_on_ready_endpoint_enters_capabilities_menu(self):
        menu, ep = _make_menu_with_endpoint(ready=True)
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[ep]):
            menu._cursor = 5  # the endpoint's position
            menu._handle_radio_key('right')

        self.assertTrue(menu.menu_state.capabilities_menu.is_active)

    def test_right_arrow_shows_the_previewed_endpoint_s_data(self):
        menu, ep = _make_menu_with_endpoint(ready=True)
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[ep]):
            menu._cursor = 5
            menu._handle_radio_key('right')

        self.assertEqual(menu._endpoint_caps['axes'], ep.axes)
        self.assertEqual(menu._endpoint_caps['streams'], ep.streams)

    def test_right_arrow_on_not_ready_endpoint_does_not_enter_menu(self):
        menu, ep = _make_menu_with_endpoint(ready=False)
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[ep]):
            menu._cursor = 5
            menu._handle_radio_key('right')

        self.assertFalse(menu.menu_state.capabilities_menu.is_active)
        self.assertTrue(menu.menu_state.radio_menu.is_active)

    def test_right_arrow_on_a_control_item_does_nothing(self):
        menu, ep = _make_menu_with_endpoint(ready=True)
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[ep]):
            menu._cursor = 0  # a control row, not an endpoint
            menu._handle_radio_key('right')

        self.assertTrue(menu.menu_state.radio_menu.is_active)

    def test_escape_from_a_preview_returns_to_radio_menu_not_main_menu(self):
        menu, ep = _make_menu_with_endpoint(ready=True)
        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[ep]):
            menu._cursor = 5
            menu._handle_radio_key('right')
            menu._handle_capabilities_key('escape')

        self.assertTrue(menu.menu_state.radio_menu.is_active)
        self.assertFalse(menu.menu_state.main_menu.is_active)

    def test_preview_does_not_clobber_the_real_connected_endpoint_s_caps(self):
        menu, ep = _make_menu_with_endpoint(ready=True)
        real_caps = {'axes': [{'name': 'real_axis'}], 'streams': []}
        menu.set_endpoint_capabilities(real_caps)  # simulates an actual, already-connected endpoint

        with patch.object(SelectionMenu, 'get_unique_endpoints', return_value=[ep]):
            menu._cursor = 5
            menu._handle_radio_key('right')  # preview a different, not-yet-connected endpoint

        self.assertNotEqual(menu._endpoint_caps, real_caps)  # showing the preview now...

        menu._handle_capabilities_key('escape')

        self.assertEqual(menu._endpoint_caps, real_caps)  # ...but restored on the way out

    def test_normal_capabilities_entry_from_main_menu_still_returns_to_main_menu(self):
        # Regression check: the pre-existing, non-preview path must be untouched.
        menu = SelectionMenu(width=320, height=240)
        menu.root = MagicMock()
        menu.set_endpoint_capabilities({'axes': [], 'streams': []})
        menu.menu_state.send('capabilities')  # the main-menu 'Capabilities' item, not a preview

        menu._handle_capabilities_key('escape')

        self.assertTrue(menu.menu_state.main_menu.is_active)


if __name__ == '__main__':
    unittest.main()
