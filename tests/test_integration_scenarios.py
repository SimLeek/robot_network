r"""
tests/test_integration_scenarios.py

            .---.
           /  o  \      "I see it. I hear it. I click it."
           \ === /            -- the night-shift AI, probably
            '---'

End-to-end integration scenarios chaining the REAL code across the
whole stack: a brain-side AI decision drives af_ai, which produces a
real MouseEvent/KeyEvent, which goes through the actual pack_obj /
unpack_obj wire serialization, and lands in the endpoint's real
DesktopHw replay handlers -- with mocks only at the true hardware
boundary (pyautogui, and the radio transport replaced by a direct
pack->unpack pipe).

These exist because unit tests on each side individually let a
button-code mismatch (brain said 1='right', endpoint said 1='left')
slip through to live hardware testing. A test that crosses the wire
catches that entire class of bug automatically.
"""

import math
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from robonet.brain.desktop_system import (
    DesktopSubSystem, AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y,
    AI_TOKEN_MOUSE_LEFT_PRESS, AI_TOKEN_MOUSE_LEFT_RELEASE,
)
from robonet.buffers.buffer_handling import pack_obj, unpack_obj


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------

def _make_brain_side(screen_width=1920, screen_height=1080):
    """A real DesktopSubSystem in AI mode, headless (no display)."""
    endpoint = MagicMock()
    endpoint.streams = [{'name': 'screen', 'type': 'video',
                        'width': screen_width, 'height': screen_height}]
    sub = DesktopSubSystem(endpoint=endpoint)
    sub._root = MagicMock()
    sub._root.menu.visible = False
    # Real frame + aspect-matched out_res: baseline viewport is an
    # identity mapping, so pixel expectations stay exact.
    sub._root.menu.last_img = np.zeros((screen_height, screen_width, 3), dtype=np.uint8)
    sub._root.menu.out_res = (640, int(640 * screen_height / screen_width))
    sub._root.displayer = None
    sub.set_input_source('ai')
    sub.start()  # binds af_ai
    return sub


def _make_endpoint_side():
    """A real DesktopHw with hardware mocked out; returns (hw, pyautogui mock)."""
    from robonet.endpoint import desktop_hardware as dh
    fake_settings = {'camera_device': None, 'mic_device': None, 'speaker_device': None}
    with patch.object(dh, 'pyautogui') as mock_pg, \
         patch.object(dh, 'settings', fake_settings), \
         patch.object(dh, 'find_camera_devices', return_value=[]), \
         patch.object(dh, 'get_first_mic_device', return_value='hw:0,0'), \
         patch.object(dh, 'get_first_speaker_device', return_value='hw:0,0'):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        mock_pg.FailSafeException = type('FailSafeException', (Exception,), {})
        hw = dh.DesktopHw()
    # Keep pyautogui mocked during replay too -- the module-level name
    # is what the handlers call.
    return hw, mock_pg


def _over_the_wire(sub, hw, mock_pg):
    """Take every event the brain 'transmitted', serialize it through
    the REAL wire format, and replay it on the endpoint -- exactly what
    the radio does, minus the radio."""
    from robonet.endpoint import desktop_hardware as dh
    events = [c.args[0] for c in sub._root.radio.burst.call_args_list]
    with patch.object(dh, 'pyautogui', mock_pg):
        for ev in events:
            wire_bytes = pack_obj(ev)          # what actually leaves the brain
            received = unpack_obj(wire_bytes)  # what the endpoint actually parses
            handler = hw.handlers[type(received).__name__]
            handler('test-brain', received)
    return events


# ---------------------------------------------------------------------------
# Scenario 1: the night-shift security guard
# ---------------------------------------------------------------------------

class TestNightShiftSecurityGuard(unittest.TestCase):
    """A monitoring dashboard throws up a red ALERT box somewhere on
    screen. The AI watching the feed finds it and clicks it --
    acknowledge the alert, no human needed at 3am.

    Chain: synthetic frame -> red-region detection -> af_ai neurons and
    tokens -> MouseEvent -> wire -> DesktopHw -> pyautogui click at the
    alert's real pixel coordinates."""

    def test_ai_sees_the_alert_and_clicks_it(self):
        sub = _make_brain_side(screen_width=1920, screen_height=1080)
        hw, mock_pg = _make_endpoint_side()

        # A 1080p frame, black, with a red alert box centered at (1500, 300)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[280:320, 1450:1550] = (255, 40, 40)  # RGB red-ish box

        # The "AI": find the red region's centroid as screen fractions.
        red_mask = (frame[:, :, 0] > 200) & (frame[:, :, 1] < 100) & (frame[:, :, 2] < 100)
        ys, xs = np.nonzero(red_mask)
        x_frac = xs.mean() / frame.shape[1]
        y_frac = ys.mean() / frame.shape[0]

        # Drive the actual AI interface -- neurons for position, tokens for the click.
        vector = [0.0] * (max(AI_NEURON_MOUSE_X, AI_NEURON_MOUSE_Y) + 1)
        vector[AI_NEURON_MOUSE_X] = x_frac
        vector[AI_NEURON_MOUSE_Y] = y_frac
        sub.af_ai.on_neuron_outputs(vector)
        sub.af_ai.on_token(AI_TOKEN_MOUSE_LEFT_PRESS)
        sub.af_ai.on_token(AI_TOKEN_MOUSE_LEFT_RELEASE)

        _over_the_wire(sub, hw, mock_pg)

        # The endpoint's cursor really went to the alert...
        (mx, my), _ = mock_pg.moveTo.call_args
        self.assertAlmostEqual(mx, 1499, delta=2)   # centroid of 1450:1550
        self.assertAlmostEqual(my, 299, delta=2)    # centroid of 280:320
        # ...and really clicked it, with the correct button name.
        mock_pg.mouseDown.assert_called_once()
        self.assertEqual(mock_pg.mouseDown.call_args.kwargs['button'], 'left')
        mock_pg.mouseUp.assert_called_once()


# ---------------------------------------------------------------------------
# Scenario 2: the alarm listener
# ---------------------------------------------------------------------------

class TestAlarmListener(unittest.TestCase):
    """The whole reason audio matters for this project: an alarm goes
    off near the endpoint, the AI hears it in the audio feed and
    reacts -- here by pressing space to pause whatever is running.

    Chain: synthetic 440Hz alarm chunk -> AISubSystem.update_audio ->
    FFT detection -> ai_key_press -> KeyEvent -> wire -> DesktopHw ->
    pyautogui key replay."""

    def test_ai_hears_a_440hz_alarm_and_reacts(self):
        from robonet.brain.ai_system import AISubSystem

        class ListeningAI(AISubSystem):
            def start(self): pass
            def stop(self): pass

        ai = ListeningAI()
        sub = _make_brain_side()
        hw, mock_pg = _make_endpoint_side()

        # A 0.1s chunk of a 440Hz alarm at 48kHz, as the real receive
        # path delivers it: 1D float32.
        sample_rate = 48000
        t = np.arange(0, 0.1, 1.0 / sample_rate)
        alarm = (0.6 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
        ai.update_audio(alarm)

        self.assertTrue(ai.has_audio)  # media-ready flag fires too

        # The "AI": dominant frequency via FFT on what it received.
        spectrum = np.abs(np.fft.rfft(ai.in_aud))
        freqs = np.fft.rfftfreq(len(ai.in_aud), d=1.0 / sample_rate)
        dominant = freqs[np.argmax(spectrum)]
        self.assertAlmostEqual(dominant, 440.0, delta=5.0)

        if 430.0 < dominant < 450.0:      # alarm recognized!
            sub.ai_key_press('space')
            sub.ai_key_release('space')

        _over_the_wire(sub, hw, mock_pg)

        mock_pg.keyDown.assert_called_once_with('space')
        mock_pg.keyUp.assert_called_once_with('space')


# ---------------------------------------------------------------------------
# Scenario 3: the polite handoff
# ---------------------------------------------------------------------------

class TestPoliteHandoff(unittest.TestCase):
    """Human and AI share one remote desktop without fighting: while
    the AI holds the controls, human input is dropped, and vice versa.
    The endpoint should replay ONLY the active source's actions."""

    def test_only_the_active_source_reaches_the_endpoint(self):
        sub = _make_brain_side()
        # Human input handlers additionally need a displayer and frame present.
        sub._root.displayer = MagicMock()
        sub._root.displayer.out_res = (1920, 1080)
        sub._root.menu.last_img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        hw, mock_pg = _make_endpoint_side()

        # AI has the controls: AI scrolls down 3; the human's frantic
        # scroll-ups must be dropped.
        sub.ai_scroll(-3)
        sub._on_mouse_scroll(+5)   # human -- dropped

        # Handoff to the human: now the reverse.
        sub.set_input_source('human')
        sub._on_mouse_scroll(+2)   # human -- passes
        sub.ai_scroll(-9)          # AI -- dropped

        _over_the_wire(sub, hw, mock_pg)

        scroll_deltas = [c.args[0] for c in mock_pg.scroll.call_args_list]
        self.assertEqual(scroll_deltas, [-3, +2])  # exactly one action per era, in order


# ---------------------------------------------------------------------------
# Scenario 4: the circle survives the wire
# ---------------------------------------------------------------------------

class TestCircleSurvivesTheWire(unittest.TestCase):
    """The AI draws a perfect circle; the endpoint receives a perfect
    circle. Verifies coordinate fidelity through fraction->pixel
    scaling, uint32 packing, and unpacking -- with no drift, no swap,
    and no off-by-one accumulation around the full circumference."""

    def test_twelve_point_circle_arrives_intact(self):
        sub = _make_brain_side(screen_width=1920, screen_height=1080)
        hw, mock_pg = _make_endpoint_side()

        expected = []
        for i in range(12):
            angle = (i / 12) * 2 * math.pi
            x_frac = 0.5 + 0.2 * math.cos(angle)
            y_frac = 0.5 + 0.2 * math.sin(angle)
            sub.ai_mouse_move(x_frac, y_frac)
            expected.append((int(min(max(x_frac, 0), 1) * 1920),
                             int(min(max(y_frac, 0), 1) * 1080)))

        _over_the_wire(sub, hw, mock_pg)

        arrived = [c.args for c in mock_pg.moveTo.call_args_list]
        # Coordinates now round-trip through the shared viewport's
        # float math (by design -- the AI sees through it), so allow a
        # sub-pixel truncation wobble. Order, count, and axes must
        # still be exact.
        self.assertEqual(len(arrived), len(expected))
        for (ax, ay), (ex, ey) in zip(arrived, expected):
            self.assertAlmostEqual(ax, ex, delta=1)
            self.assertAlmostEqual(ay, ey, delta=1)


if __name__ == '__main__':
    unittest.main()
