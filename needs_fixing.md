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
  Simleek: I think states are fixed, but now the meny may not update due 
  to the changed to _robot_capabilities_handler. I remember that was
  tricky, and I didn't want to send and retrieve through the dict in
  case it might get another endpoint. Now it sends/retrieves through
  the dict. The 'code cleanup' may need to be reverted. Main commit
  382508458f40d5e2515391469b8899fa9c57030e does not have this issue.

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

- **Zoom/pan implemented, needs AI controls.**
  Ctrl+Shift+2 toggles edit mode; scroll zooms (1.0 = whole frame
  visible, letterboxed to preserve aspect ratio; max = 1 source pixel
  per display pixel, computed from actual source/display resolution,
  not hardcoded); hovering within 10% of an edge in edit mode pans,
  checked every frame so it keeps panning while the mouse just sits
  near the edge. robonet/brain/util/viewport.py + tests/test_viewport.py
  has the crop/scale/pad math (handles the letterboxing case correctly
  -- the ideal crop region can genuinely exceed the source frame in one
  dimension when source/display aspect ratios differ, which needed
  care to get right). NOT yet done: AI neuron/token controls for
  zoom/pan/reset, mentioned as wanted but out of scope for this round
  given how much else changed -- same af.bind_ai_neuron/bind_ai_token
  pattern used elsewhere in this codebase should apply directly once
  it's time to wire it up.

- **The "needs 2 brain runs to connect" bug, root cause found.**
  _robot_capabilities_handler's inline fallback lookup (for when an
  endpoint isn't in self._endpoints yet) required v.endpoint_type ==
  obj.endpoint_type to find the scanner's record -- but on the very
  first RobotCapabilities message for an endpoint, the scanner's own
  record still has endpoint_type='unknown' (it doesn't learn the real
  type until this very message). The match always failed on that first
  receipt, hit the "Could not find endpoint" error-and-return path, and
  never set capabilities_received -- no matter how many times the
  endpoint retried (matching "asked for capabilities" repeating
  forever). enrich_endpoint already had the correct fix (hostname-only
  matching); the handler was just duplicating the lookup with an extra,
  wrong condition instead of using it. No prior test coverage existed
  for this handler at all -- added
  tests/test_radio_capabilities_handler.py.
  Simleek: Actually it happened again, so this will need more testing later.

- **WiFi auto-connecting when it shouldn't** -- reported but explicitly
  deprioritized (Simleek: "not a super important issue... later todo
  stuff"), and not confirmed against real hardware yet (only tested
  against the sandbox endpoint so far). Revisit once tested against an
  actual robot on the network.
  Simleek: This was maybe_auto_connect being too permissive. I fixed it by 
  adding checks. I expected it to connect on localhost or wired, not wifi.
  Updated 3 tests in test_radio_autoconnect.py that predated this fix and
  didn't set start_mode to match their priority list -- the new check
  correctly requires the current mode to actually be in that list.

- **AI passthrough implemented on a new branch (ai_passthrough, off
  main).** DesktopSubSystem gets a dedicated ActionFactory (af_ai),
  independent of DisplaySubSystem's af_thru/af_edit (those only exist
  with a display window; af_ai works headless). Mouse position drives
  through 2 neurons (AI_NEURON_MOUSE_X/Y, threshold=0), buttons through
  6 tokens (press/release x left/right/middle); keyboard is direct
  methods (ai_key_press/release) since there are too many possible keys
  for a fixed token enum. input_source ('human'/'ai') gates both paths
  symmetrically -- whichever is active gets sent, the other silently
  dropped, since both driving the same remote cursor at once would just
  fight each other. AI mouse coordinates are direct screen fractions
  (_ai_frac_to_pixel), deliberately not routed through the human path's
  zoom/pan-aware inverse_map -- an AI isn't looking through a human's
  local viewport, so it shouldn't be affected by whatever that's zoomed/
  panned to. examples/ai_passthrough_demo.py demonstrates it: circular
  mouse motion, one right-click, one F11 tap, and a sine-wave test tone
  (new AUDIO_SOURCE_SINE_TEST sentinel in streamer_unencrypted.py,
  audiotestsrc-based, no real mic needed) sent as the brain's own
  outbound audio via MenuSubSystem's already-public gst_sender. Found
  and fixed two real, blocking bugs in ServerSystem along the way:
  async_loops() called self.displayer.run(self) unconditionally, which
  crashed any headless session immediately despite the constructor's own
  `assert displayer or ai` explicitly allowing displayer=None; and
  self.ai's start/stop/async_loops were never called anywhere at all, so
  an AI subsystem's coroutines would never actually get scheduled. No
  prior test coverage existed for ServerSystem -- added
  tests/test_server_system.py.

- **AI wasn't receiving audio at all.** AISubSystem only had
  update_frame/in_img -- no audio equivalent existed. send_frames_always
  already sent both video and audio to a human display but only video to
  self.root.ai. The video side (menu overlay included, since it's the
  same already-composited frame) was correctly shared -- just audio was
  missing entirely. Added update_audio/in_aud to AISubSystem, matching
  update_frame/in_img exactly, and the corresponding call in
  send_frames_always. No prior test coverage existed for either
  AISubSystem or send_frames_always -- added tests/test_ai_system.py and
  tests/test_send_frames_always.py.

- **Fixed from IRL testing round (re-applied after an environment reset
  lost the first attempt before it could be committed):**
  1. AI right-click sent the wrong button. _AI_BUTTON_NAMES used a
     sequential 0/1/2 convention; the endpoint's own _MOUSE_BUTTON_NAMES
     uses pyglet's bitmask values (1/2/4). code=1 ('right' under the old
     table) decoded as 'left' on the endpoint. Fixed to match exactly;
     added a cross-check test importing both tables directly so this
     can't silently regress again.
  2. F11 toggled displayarray's own fullscreen instead of reaching the
     endpoint -- moderngl_window's base Window class binds F11 to
     fullscreen-toggle by default, before pass_through_cb ever sees it.
     Disabled via wnd.fullscreen_key = None (moderngl_window's own
     documented mechanism), applied once the window becomes reachable
     (guarded/idempotent in DisplaySubSystem.run_once). F1 has no such
     special handling in moderngl_window at all -- if still not working,
     most likely reaching the endpoint fine but having no visible effect
     there (F1 isn't bound to anything by default on most desktops).
  3. AISubSystem had no way to know if real video/audio had actually
     started flowing -- added has_video/has_audio flags. Demo now waits
     on both before starting its sequence -- log showed a ~9s gap
     between connecting and audio actually flowing, so the sine tone
     was very likely never actually heard.
  4. Mouse "offset" after the AI demo ends is very likely not a bug --
     absolute positioning means the remote cursor snaps to wherever the
     human's own local mouse currently sits the moment control returns,
     regardless of where the AI left it. Worth confirming next round.
  5. Checked test coverage for the discovery issue: the endpoint's own
     RobotState machine has a clean one-go test already
     (test_normal_full_handshake). Nothing exercises the real
     RadioSubSystem/NetworkScanner discovery-then-capabilities flow
     together end to end -- existing tests only unit-test individual
     handlers with hand-built fixtures. Worth building if it recurs.

  Also: this session's sandbox lost several installed packages partway
  through (zmq, python-statemachine, gstreamer GObject bindings,
  PyV4L2Cam + libv4l-dev, PortAudio, displayarray + its GL/window stack)
  along with all uncommitted working-tree changes -- unrelated to any
  code issue, just worth knowing the sandbox itself isn't durable
  storage. All reinstalled and 398/398 tests confirmed passing again.

- **Pre-connection capability preview.** The 'radio'/'settings'/
  'capabilities' menu structure Simleek described already existed
  (MenuStateMachine, left/right key routing all the way from
  moderngl_window's Keys through handle_keyboard to
  _handle_capabilities_key, footer hint bars) -- it just had no
  pre-connection path. set_endpoint_capabilities was only ever called
  post-connection, even though ep.axes/ep.streams are already populated
  on the Endpoint object the moment RobotCapabilities arrives during
  discovery, well before any connection attempt.
  Added: right-arrow on a highlighted (ready) endpoint in the radio menu
  now previews its capabilities via a new preview_endpoint_capabilities
  path and preview_capabilities/leave_to_radio state transitions,
  returning to the radio menu (not main_menu) on escape. Preserves
  whatever the actually-connected endpoint's capabilities are separately
  (_connected_endpoint_caps) so a preview never clobbers them. No prior
  test coverage existed for any of this -- added
  tests/test_capabilities_preview.py.

- **Sine tone still not heard -- correction of scope.** My earlier fix
  (sounddevice output device selection) was entirely brain-side, but
  the demo's sine tone travels brain->endpoint and plays on the
  endpoint's own speaker via alsasink in _AudioRecvPipeline, using
  examples/desktop/desktop_endpoint.py (robonet/endpoint, not
  robonet/brain) -- a completely different code path. The endpoint-side
  device selection (get_first_speaker_device -> find_speaker_devices,
  which does correctly prepend 'default' when hardware is found) looked
  structurally sound on read-through, so the exact remaining cause is
  still open -- added visible logging of the actual device string used
  ("[hardware] using speaker device: ...") since there was previously
  none, which should narrow it down next test.

- **Found and fixed the actual cause of "Capabilities" missing from the
  main menu after connecting.** get_unique_endpoints deduped by raw
  object id. The scanner's stale-removal path
  (NetworkScanner._unregister, triggered when an endpoint isn't seen
  for scan_interval*2 -- plausible on any transient network blip) fully
  removes an endpoint from by_ip/by_hostname; if it's rediscovered
  afterward, a *fresh* Endpoint object gets created for it. Meanwhile
  RadioSubSystem._endpoints still holds the old, capabilities-populated
  object under a different key (hostname:endpoint_type vs bare
  hostname/ip), so both end up in the merged dict as distinct-by-id
  entries. Connecting to the wrong (fresh, empty) one meant
  ep.axes/ep.streams were empty, so _connect's own
  `if getattr(ep, 'axes', None) or getattr(ep, 'streams', None)` gate
  around set_endpoint_capabilities was never satisfied. This is very
  likely the same root cause behind Simleek's earlier "it happened
  again" note about _robot_capabilities_handler.
  Fixed: get_unique_endpoints now dedupes by logical identity (hostname,
  falling back to ip) instead of object id, preferring whichever
  instance actually has capabilities_received=True when two collide.
  Added tests/test_endpoint_dedup.py -- no prior coverage caught this at
  all, since existing tests only ever populated one object per logical
  endpoint.

- **Streams now include the speaker (audio output), plus I/O tagging.**
  build_desktop_capabilities only listed screen and mic -- both inputs
  from the endpoint's perspective -- with no speaker (output) entry at
  all, despite streams representing both directions. Added a speaker
  stream (audio, 48000Hz, 1ch) and an `io: 'I'|'O'` field on every
  stream entry; _fmt_stream now displays it in the capabilities menu.

- **AV source switching (desktop capture vs camera, mic selection,
  speaker selection) explicitly deprioritized per Simleek's own
  request** -- static endpoint-side configuration (already supported via
  MultiAVRobotHardware's camera/mic/speaker constructor params) is
  sufficient for now; connecting to an endpoint should just work without
  needing in-session reconfiguration. Revisit if it becomes a real need.

- Simleek's own assessment: most remaining work is endpoint code, then
  some menu code, very little if any brain code.
