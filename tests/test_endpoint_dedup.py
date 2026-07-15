"""
tests/test_endpoint_dedup.py

Regression test for the actual bug behind "capabilities missing from
the main menu after connecting": get_unique_endpoints deduped by raw
object id, but the scanner can end up tracking a *different* Endpoint
object for what's logically the same endpoint -- e.g. after a
transient stale-timeout removal (a network blip exceeding
scan_interval*2) and rediscovery, which creates a fresh, empty object
while the old, capabilities-populated one is still referenced
elsewhere. Both then show up in the merged endpoint dict under
different keys, and connecting to the wrong (empty) one meant
set_endpoint_capabilities never got called at all.
"""

import unittest
from unittest.mock import MagicMock

from robonet.brain.util.selection_menu import SelectionMenu


def _make_endpoint(hostname='desk1', ip='10.0.0.5', capabilities_received=False,
                    axes=None, streams=None, endpoint_type='desktop'):
    return MagicMock(
        hostname=hostname, ip=ip, endpoint_type=endpoint_type,
        capabilities_received=capabilities_received,
        axes=axes if axes is not None else [],
        streams=streams if streams is not None else [],
    )


class TestGetUniqueEndpointsDedup(unittest.TestCase):

    def _make_menu(self, endpoints_dict):
        menu = SelectionMenu(width=320, height=240)
        menu._endpoints = endpoints_dict
        return menu

    def test_two_distinct_endpoints_both_appear(self):
        ep1 = _make_endpoint(hostname='desk1', ip='10.0.0.5')
        ep2 = _make_endpoint(hostname='desk2', ip='10.0.0.6')
        menu = self._make_menu({'desk1:desktop': ep1, 'desk2:desktop': ep2})

        result = menu.get_unique_endpoints()

        self.assertEqual(len(result), 2)

    def test_stale_rediscovery_scenario_prefers_the_ready_object(self):
        # The exact bug: same logical endpoint (same hostname), but two
        # separate object instances -- one still has real capabilities
        # from before a stale removal+rediscovery, the other is the
        # fresh, empty one the scanner created afterward.
        stale_but_ready = _make_endpoint(hostname='desk1', ip='10.0.0.5',
                                        capabilities_received=True,
                                        axes=[{'name': 'mouse_move_x'}],
                                        streams=[{'name': 'screen'}])
        fresh_and_empty = _make_endpoint(hostname='desk1', ip='10.0.0.5',
                                        capabilities_received=False,
                                        axes=[], streams=[])
        menu = self._make_menu({
            'desk1:desktop': stale_but_ready,
            '10.0.0.5': fresh_and_empty,  # what the scanner's by_ip would contribute after rediscovery
        })

        result = menu.get_unique_endpoints()

        self.assertEqual(len(result), 1)  # collapsed to one, not two
        self.assertIs(result[0], stale_but_ready)  # the ready one wins, not whichever came first

    def test_prefers_ready_regardless_of_dict_order(self):
        stale_but_ready = _make_endpoint(hostname='desk1', capabilities_received=True)
        fresh_and_empty = _make_endpoint(hostname='desk1', capabilities_received=False)
        # Empty one inserted first this time -- order must not matter
        menu = self._make_menu({
            'first_key': fresh_and_empty,
            'second_key': stale_but_ready,
        })

        result = menu.get_unique_endpoints()

        self.assertEqual(len(result), 1)
        self.assertIs(result[0], stale_but_ready)

    def test_falls_back_to_ip_when_hostname_is_empty(self):
        ep1 = _make_endpoint(hostname='', ip='10.0.0.5', capabilities_received=True)
        ep2 = _make_endpoint(hostname='', ip='10.0.0.5', capabilities_received=False)
        menu = self._make_menu({'a': ep1, 'b': ep2})

        result = menu.get_unique_endpoints()

        self.assertEqual(len(result), 1)
        self.assertIs(result[0], ep1)

    def test_neither_ready_keeps_the_first_one_seen(self):
        ep1 = _make_endpoint(hostname='desk1', capabilities_received=False)
        ep2 = _make_endpoint(hostname='desk1', capabilities_received=False)
        menu = self._make_menu({'a': ep1, 'b': ep2})

        result = menu.get_unique_endpoints()

        self.assertEqual(len(result), 1)
        self.assertIs(result[0], ep1)  # stable choice: whichever came first


if __name__ == '__main__':
    unittest.main()
