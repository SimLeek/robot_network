"""
examples/diagnose_desktop_capture.py

Standalone diagnostic: captures snapshots at each stage of desktop
capture in isolation, so a blank/broken result can be traced to a
specific stage instead of guessing across the whole feeder+loopback+
network chain at once.

    python -m examples.diagnose_desktop_capture

Writes to ./capture_diagnostics/:
  01_raw_ximagesrc.png   -- straight from ximagesrc, no loopback involved
  02_loopback_device.png -- read back from the v4l2loopback device
                            (only meaningful once a video feeder is
                            actively writing to it -- run this while
                            desktop_endpoint.py is running)
  03_loopback_audio.wav  -- a few seconds from the ALSA loopback capture
                            side (only meaningful while an audio feeder
                            is running)

If 01 looks right but 02 is blank/black: the bug is in the feeder's
loopback-writing path (DesktopVideoFeeder), not X11/ximagesrc itself.
If 01 is already blank/black: it's an X11/compositor capture issue
upstream of anything this project's code does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

OUT_DIR = 'capture_diagnostics'


def _run_pipeline_for_one_buffer(desc: str, timeout_s: float = 5.0) -> bytes | None:
    """Runs a pipeline until one buffer reaches an appsink named 'sink',
    then tears it down. Returns the raw buffer bytes, or None on
    timeout/error."""
    pipeline = Gst.parse_launch(desc)
    sink = pipeline.get_by_name('sink')
    result = {}

    def on_sample(s):
        sample = s.emit('pull-sample')
        if sample is not None:
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if ok:
                result['data'] = bytes(mapinfo.data)
                result['caps'] = sample.get_caps()
                buf.unmap(mapinfo)
        loop.quit()
        return Gst.FlowReturn.OK

    sink.connect('new-sample', on_sample)
    pipeline.set_state(Gst.State.PLAYING)

    loop = GLib.MainLoop()
    GLib.timeout_add(int(timeout_s * 1000), loop.quit)
    loop.run()

    pipeline.set_state(Gst.State.NULL)
    return result.get('data'), result.get('caps')


def capture_raw_ximagesrc(out_path: str):
    print('[1/3] Capturing directly from ximagesrc (no loopback)...')
    desc = ('ximagesrc use-damage=false ! videoconvert ! '
           'video/x-raw,format=RGB ! pngenc ! appsink name=sink emit-signals=true sync=false')
    data, _caps = _run_pipeline_for_one_buffer(desc)
    if data is None:
        print('  FAILED: no buffer received (timeout). ximagesrc itself may not be working '
             '-- check DISPLAY is set and this is a real X11 session.')
        return False
    with open(out_path, 'wb') as f:
        f.write(data)
    print(f'  wrote {out_path} ({len(data)} bytes)')
    return True


def capture_loopback_device(device: str, out_path: str):
    print(f'[2/3] Capturing from loopback device {device}...')
    desc = (f'v4l2src device={device} num-buffers=1 ! videoconvert ! '
           f'video/x-raw,format=RGB ! pngenc ! appsink name=sink emit-signals=true sync=false')
    data, _caps = _run_pipeline_for_one_buffer(desc)
    if data is None:
        print(f'  FAILED: no buffer received from {device}. Is desktop_endpoint.py '
             'actually running right now with a video feeder active?')
        return False
    with open(out_path, 'wb') as f:
        f.write(data)
    print(f'  wrote {out_path} ({len(data)} bytes) -- open it and compare to 01_raw_ximagesrc.png')
    return True


def capture_loopback_audio(hw_device: str, out_path: str, seconds: float = 3.0):
    print(f'[3/3] Capturing {seconds}s of audio from {hw_device}...')
    desc = (f'alsasrc device={hw_device} ! audioconvert ! '
           f'wavenc ! filesink location={out_path}')
    pipeline = Gst.parse_launch(desc)
    pipeline.set_state(Gst.State.PLAYING)
    time.sleep(seconds)
    pipeline.send_event(Gst.Event.new_eos())
    time.sleep(0.5)
    pipeline.set_state(Gst.State.NULL)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 44:  # more than just a WAV header
        print(f'  wrote {out_path} ({os.path.getsize(out_path)} bytes) -- play it back and listen')
        return True
    print(f'  FAILED: {out_path} is empty or missing -- is an audio feeder actually running?')
    return False


if __name__ == '__main__':
    os.makedirs(OUT_DIR, exist_ok=True)

    ok1 = capture_raw_ximagesrc(os.path.join(OUT_DIR, '01_raw_ximagesrc.png'))

    if len(sys.argv) > 1:
        loopback_video_device = sys.argv[1]
    else:
        from robonet.endpoint.desktop_capture import find_v4l2loopback_device
        loopback_video_device = find_v4l2loopback_device()

    if loopback_video_device:
        capture_loopback_device(loopback_video_device, os.path.join(OUT_DIR, '02_loopback_device.png'))
    else:
        print('[2/3] No v4l2loopback device found -- pass its path as an argument, '
             'e.g. python -m examples.diagnose_desktop_capture /dev/video42')

    if len(sys.argv) > 2:
        loopback_audio_hw = sys.argv[2]
        capture_loopback_audio(loopback_audio_hw, os.path.join(OUT_DIR, '03_loopback_audio.wav'))
    else:
        print('[3/3] Skipped -- pass the ALSA capture-side hw device as a second argument '
             'to also test audio, e.g. python -m examples.diagnose_desktop_capture /dev/video42 hw:3,1,0')

    print(f'\nDone. Check {OUT_DIR}/ -- if 01 looks right but 02 is blank, the bug is in '
         'DesktopVideoFeeder\'s loopback-writing path, not X11/ximagesrc.')
