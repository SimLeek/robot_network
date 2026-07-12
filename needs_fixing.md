# Needs Fixing

## likely real bugs

These need testing to be confirmed as real bugs before fixing.

- **robonet/brain/radio_system.py, check_wifi_connected_linux()**:
  `subprocess.check_output(["iwgetid", "-r"], shell=True)` -- shell=True
  with a list arg means only args[0] ("iwgetid") is used as the shell
  command string; "-r" is passed to /bin/sh, not to iwgetid. Probably
  doesn't return what it's meant to.

- **robonet/util.py, get_connection_info()**: after the first subprocess
  call computes `devices` from the wifi-device query, the second call
  (for `current_connection`) re-assigns `devices = list(filter(None,
  result.stdout.split('\n')))` using `result` from the *second* call --
  looks like the device list gets silently overwritten with the
  connection-name grep output instead of the actual device list.

- **robonet/adhoc/util.py, set_hotspot()**: `nmcli con modify
  ... 802-11-wireless.mode adhoc_pair ...` -- `adhoc_pair` isn't a valid
  nmcli wireless mode (valid values: infrastructure/ap/adhoc/mesh). Looks
  like a search-and-replace accident; probably meant `adhoc`. Would make
  hotspot setup fail if actually exercised. `client.py`'s
  `connect_hotspot()` uses `adhoc` correctly, so the two are inconsistent
  with each other too.

- **robonet/brain/util/desktop_window_config.py,
  make_window_config_for_server_main()**: calls `sm.handle_mouse_move`,
  `sm.handle_mouse_click`, `sm.handle_mouse_scroll`, `sm.send_key` --
  none of these exist anywhere on ServerSystem (grepped the whole repo).
  This function would raise AttributeError immediately if ever called.
  Looks superseded by `make_window_config_for_server` (no `_main` suffix,
  used by display_system.py), which doesn't have this problem. Possibly
  safe to delete outright once confirmed unused.

- **tests/test_buffers.py**: imports `CamFrame` from
  `robonet.buffers.buffer_objects`, but the class is named `CVCamFrame`.
  The whole file fails at import time as-is (confirmed: ran it, ImportError).

## Todo

these fixes should be implemented but are either large tasks or are blocked.

- **examples/setup_vnc_client.sh**: old boilerplate code that needs to be 
  replaced with code that sets up a permanent endpoint by booting endpoint 
  code on startup.

- **Multiple endpoint types on one machine collide on port.**
  `endpoint/settings.py`'s `our_port` (9998) is a single fixed value --
  `RobotRadio.__init__` does `self._dish.bind(f"udp://0.0.0.0:{our_port}")`
  unconditionally regardless of `endpoint_type`. Running
  `robot_endpoint.py` and `desktop_endpoint.py` on the same machine at the
  same time means both try to bind the same UDP port; the second one to
  start fails. Proposed fix: a small shared `ENDPOINT_TYPE_PORT_OFFSETS =
  {'robot': 0, 'desktop': 1}` (in `robonet/util.py`, already imported by
  both sides), endpoint binds to `our_port + offset[endpoint_type]`, and
  brain's `RadioSubSystem.connect_additional(ip)` connects to one port per
  offset instead of a single fixed port per ip. Not done yet because it
  also touches `_probed_ips` (currently a set of bare ips, would need to
  become ip:port pairs), `_disconnect_all`, `connect_to`, `_on_scanner_lost`,
  and `NetworkScanner._probe_port`'s TCP liveness check -- real surface
  area across both sides' core discovery path, and not something I could
  verify here without two real machines (or at least two real processes
  actually exchanging UDP over a real interface, not just importable
  Python). GStreamer video and audio streaming also uses fixed ports 
  that may need to be updated based on endpoint types.

- **ADHOC mode likely has the same class of gap wired mode had, unfixed.**
  Noticed this while confirming nothing referenced the now-deleted
  `adhoc_pair/client.py`: the brain side creates the wifi hotspot
  (`set_hotspot()`) and broadcasts its connection info
  (`lazy_pirate_send_con_info()`), but nothing in the current
  `robonet/endpoint/` code listens for that broadcast or joins the
  hotspot -- the old `adhoc_pair/client.py` did exactly that (via
  `lazy_pirate_recv_con_info()` + `connect_hotspot()`), but on the older,
  now-deleted REQ/REP+unicast pattern, and it was never actually called
  from `RobotRadio`/`RobotNode` either. So joining ADHOC mode's
  brain-created hotspot probably has the identical problem WIRED mode had
  before this session's fix: the endpoint's wifi interface has no reason
  to associate with a hotspot it's never told to join. Didn't fix this now -- flagging it since it's a direct
  parallel to what was just fixed for wired, and the fix shape would
  likely mirror it (an endpoint-side function using current-architecture
  primitives, gated behind a settings flag, called from
  `RobotRadio.__init__`), but wanted to confirm the diagnosis and get
  agreement on scope before touching ADHOC mode's actual wifi-joining
  behavior. (Simleek: the old adhoc connection method was tested on real hardware, 
  so robot radio should use that working code. RobotRadio adhoc connections were not 
  used or tested. So, I agree on the diagnosis and scope.)

- **Audio neurons/blocksize -- now implemented, needs testing.** Original idea was
  letting an AI's raw neuron-output tensor drive audio *output* (e.g.
  synthesized speech/tone) the same way `SparseVectorBuffer` drives a
  robot's motors today, and treating mic input as just another received
  stream on the same footing as camera video (which it already mostly
  is -- `CamMicSpkRobotHardware` already pairs mic with camera
  symmetrically). Built out this session as `robonet/audio_io.py`
  (`audio_neuron_spec`/`blocksize_for_max_hz` -- the blocksize<->Hz math)
  and `robonet/brain/ai_audio.py` (`setup_ai_audio_input`/
  `setup_ai_audio_output` -- the actual PipeWire-virtual-device +
  sounddevice + GstSender/GstReceiver redirection). Logic is fully unit
  tested (mocked subprocess/sounddevice throughout), but the PipeWire
  device-naming assumption in `ai_audio.py`'s module docstring (that
  `pw-loopback`'s `node.name` is what sounddevice/PortAudio and
  GStreamer's alsasrc/alsasink both see as the device string) could not
  be verified against a live PipeWire server in this environment --
  flagged clearly in that docstring, with `python -m sounddevice` given
- **Audio should use direct PCM streams, not FFT.** `AudioBuffer` currently
  models per-channel FFT data (complex128). Simleek: direct streams
  perform better with AI than FFT. Not touched yet -- needs a real
  redesign of `AudioBuffer` (or a new buffer type) plus whatever
  consumes `display_fftnet`'s FFT-based visualization today.

- **Localhost mode probably shouldn't require matching PSKs.** Same
  machine talking to itself has no real encryption need; the plain
  (unencrypted) radio path in `todo/plain_radio.py` may be worth
  reviving specifically for NetMode.LOCALHOST instead of staying
  archived.

- **Handshake state machine rebuilt to never dead-end.** Real logs
  showed retransmission bursts (each step arriving 2-4x) hitting
  "received while in X state" errors and dropping the message --
  RobotState now defines every message from every state it could
  plausibly arrive in: self-loop (no-op) if already at/past the target,
  forward-jump if earlier, and only WhoAreYou can move backward
  (streaming -> greeting, a new brain session replacing a dead one).
  Exhaustive matrix + chaos-scenario tests in
  tests/test_endpoint_handshake.py.

- **GstSender still has no video source on the brain's own outbound
  side** (`device=None` in the logs) when the brain machine has no
  camera configured -- currently silently skips video, sends audio from
  'default'. Probably fine (graceful degradation) but not confirmed
  intentional vs. an oversight.

- **RobotCapabilities.axes()/.streams() for real robots** (not desktop)
  still worth spot-checking against this same "never report an
  inert-looking capability set" standard now that desktop's been fixed.

- **Desktop capture dropped the v4l2loopback/ALSA-loopback approach
  entirely.** Confirmed via diagnose_desktop_capture.py: raw ximagesrc
  produced a real screenshot, both loopback devices came back blank --
  the custom appsink/appsrc bridge had a real bug somewhere (never
  isolated exactly where), and the loopback round-trip added a kernel
  module dependency for no benefit anyway. GstSender's video/audio
  pipelines now support VIDEO_SOURCE_XIMAGESRC/AUDIO_SOURCE_DESKTOP_MIX
  sentinels, capturing directly. `DesktopHw` no longer takes
  capture_width/height/fps -- desktop video resolution/fps are now
  controlled the same way as any camera, via cam_res/cam_fps.
  `examples/teardown_desktop_capture.sh` removes the now-unused kernel
  modules from a machine that ran the old setup script.

- **Desktop key mapping was hardcoded to GLFW's keycode numbers, but
  the actual runtime backend is pyglet** (confirmed by reading
  moderngl_window/pyglet source directly -- pyglet's F4 is 0xffc1,
  GLFW's is 293). This meant every special key except plain ASCII
  silently failed to forward at all. `keycode_to_pyautogui` now builds
  its mapping dynamically from the actual runtime `keys` object instead
  of a hardcoded table, matching the pattern `handle_keyboard`'s own
  nav_map already used. Not fully closed: `LEFT_ALT`/`RIGHT_ALT` aren't
  exposed by moderngl_window's Keys wrapper at all (only
  LEFT_SHIFT/RIGHT_SHIFT/LEFT_CTRL are), so Alt as a *key itself* can't
  be named this way -- but `KeyModifiers.alt` is a separately-populated
  field (confirmed in moderngl_window's source) that should already
  carry Alt's held/not-held state independent of this, so Alt+F4 should
  work now that F4 itself resolves correctly. Worth confirming on real
  hardware.

- **Audio format mismatch, found via Simleek's own NaN/garbage-value
  debugging.** The receive pipeline's caps filter didn't specify
  format=, so audioconvert could negotiate anything (apparently S16LE
  in practice) while `_pull_chunk` hardcoded `np.float32` reading the
  raw bytes back -- 16-bit PCM samples reinterpreted as 32-bit floats,
  which is exactly the "mostly zero, occasional NaN, values like
  9e-41" pattern that produces garbage. Fixed by explicitly requesting
  format=F32LE in the caps filter.

- **Mouse buttons had the same GLFW-vs-pyglet mismatch as keys.**
  `_MOUSE_BUTTON_NAMES` was `{0: left, 1: right, 2: middle}` (GLFW's
  sequential indices), but pyglet's mouse buttons are bitmask values
  (confirmed via pyglet.window.mouse source: LEFT=1, MIDDLE=2, RIGHT=4)
  -- every left click was flipped to a right click. Fixed to
  `{1: left, 4: right, 2: middle}`. Unlike keys, moderngl_window doesn't
  expose a normalized mouse-button abstraction, so this stays
  pyglet-specific; a different backend would need revisiting.

- **Mouse position swap was applied in the wrong place last round.**
  Swapping the call-site arguments to on_mouse_move fixed positional
  correctness but broke which scaling factor (screen width vs height)
  applied to which axis, since the scaling code still assumed the
  original argument order. Redone cleanly: the swap now happens once,
  at the source, into clearly-named x_frac/y_frac, with no further
  swapping downstream. Also added clamping to 0..1 before scaling (the
  mouse can legitimately report fractions outside that range when it's
  over letterboxing/padding around the captured image).

- **Two open items, lower confidence:**
  - Alt+F4 giving "error, terminal emulator not set" on LXDE sounds
    like an LXDE keybinding/config issue (LXDE intercepting Alt+F4 for
    something other than "close window", missing its configured
    terminal), not a robonet bug -- the key itself likely reached the
    endpoint fine now that the keycode mapping is fixed.
  - Real mouse/keyboard (not through robonet) reportedly landing wrong
    after a remote-control session. Nothing in this code alters
    system-level cursor/display settings, so if it's real it's more
    likely a pyautogui/X11 quirk than something robonet is doing --
    but not confirmed either way. Worth knowing whether the offset is a
    fixed amount or scales with position if this comes up again.
