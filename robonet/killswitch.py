"""
robonet/killswitch.py -- stop every robot_network process on this machine.

    python -m robonet.killswitch            # listen for Ctrl+Alt+Shift+F12 (physical keyboard)
    python -m robonet.killswitch --now      # stop them right away (e.g. over ssh)
    python -m robonet.killswitch --list     # show what would be stopped

Run the listener in the X session of the endpoint machine (DISPLAY=:0).
The endpoint always refuses this combo (KILL_COMBO in desktop_hardware),
and robonet has no Alt key, so nothing sent through robonet can trigger it.

"robot_network process" = a python process whose command line or working
directory mentions robot_network or robonet (brain, endpoint, AI bridge
clients started from the repo), excluding this process. After stopping
them, every key/button robonet can press is released, since a killed
endpoint can't release what it was holding.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

KILL_COMBO = ('ctrl', 'alt', 'shift', 'f12')
_MARKERS = ('robot_network', 'robonet')


def find_processes() -> list[tuple[int, str]]:
    me = {os.getpid(), os.getppid()}
    found = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) in me:
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as fh:
                cmd = fh.read().replace(b'\0', b' ').decode(errors='replace').strip()
            cwd = os.readlink(f'/proc/{pid}/cwd')
        except OSError:
            continue
        if 'python' not in cmd.split(' ')[0] or 'killswitch' in cmd:
            continue
        if any(m in cmd or m in cwd for m in _MARKERS):
            found.append((int(pid), cmd))
    return found


def release_inputs() -> None:
    try:
        import pyautogui
        from robonet.desktop_control_spec import AI_SUPPORTED_KEYS
    except Exception as e:
        print(f'[killswitch] could not release inputs: {e}', file=sys.stderr)
        return
    pyautogui.PAUSE = 0.0
    pyautogui.FAILSAFE = False
    for key in AI_SUPPORTED_KEYS:
        try:
            pyautogui.keyUp(key)
        except Exception:
            pass
    for button in ('left', 'right', 'middle'):
        try:
            pyautogui.mouseUp(button=button)
        except Exception:
            pass


def stop_all(grace_s: float = 3.0, release: bool = True) -> list[tuple[int, str]]:
    procs = find_processes()
    for pid, _ in procs:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and any(os.path.exists(f'/proc/{p}') for p, _ in procs):
        time.sleep(0.1)
    for pid, _ in procs:
        if os.path.exists(f'/proc/{pid}'):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if release and os.environ.get('DISPLAY'):
        release_inputs()
    return procs


def listen() -> None:
    """Grab Ctrl+Alt+Shift+F12 on the X root window; stop everything on press."""
    from Xlib import X, XK, display
    d = display.Display()
    root = d.screen().root
    code = d.keysym_to_keycode(XK.string_to_keysym('F12'))
    mods = X.ControlMask | X.Mod1Mask | X.ShiftMask
    # Also grab with NumLock (Mod2) / CapsLock (Lock) on, or the grab misses.
    for extra in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask):
        root.grab_key(code, mods | extra, True, X.GrabModeAsync, X.GrabModeAsync)
    d.sync()
    print(f'[killswitch] listening for {"+".join(KILL_COMBO)} on {os.environ.get("DISPLAY")}', flush=True)
    while True:
        ev = d.next_event()
        if ev.type == X.KeyPress and ev.detail == code:
            procs = stop_all()
            print(f'[killswitch] stopped {len(procs)}: ' + '; '.join(f'{p} {c[:80]}' for p, c in procs), flush=True)
            subprocess.run(['notify-send', 'robonet killswitch', f'stopped {len(procs)} processes'],
                           check=False, capture_output=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--now', action='store_true', help='stop everything now and exit')
    ap.add_argument('--list', action='store_true', help='list matching processes and exit')
    args = ap.parse_args()
    if args.list:
        for pid, cmd in find_processes():
            print(pid, cmd)
    elif args.now:
        procs = stop_all()
        print(f'stopped {len(procs)}')
        for pid, cmd in procs:
            print(pid, cmd)
    else:
        listen()


if __name__ == '__main__':
    main()
