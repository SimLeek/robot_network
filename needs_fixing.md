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

- **Fixed device-selection logging placement and DesktopHw's config
  override bug.** Last round's speaker-device log line landed in
  CamMicSpkRobotHardware.__init__ -- a completely different class
  DesktopHw doesn't use at all, which is why it never appeared at the
  right time. DesktopHw.__init__ took no camera/speaker arguments
  whatsoever and unconditionally called find_camera_devices()[0] /
  get_first_speaker_device(), ignoring any possible configuration
  entirely. Now accepts camera=/speaker= constructor args, checks new
  camera_device/speaker_device settings if not given, and only
  auto-detects as a last resort -- logged at construction time (real
  startup), not connection time. Added
  tests/test_desktop_hw_device_priority.py.

- **Sine tone still unexplained, and the brain-side sounddevice
  playback path may never have actually worked at all.** Simleek can
  see audio data arriving on the brain side (the numpy-buffer waveform
  display) but has never once heard actual sound through real
  speakers, and would expect to see an active stream in pavucontrol if
  sounddevice.OutputStream were genuinely producing output -- never
  observed. The one time brain->endpoint audio was confirmed audible
  was on a different robot (basicpibot) using GStreamer for endpoint-
  side playback, not sounddevice for brain-side playback. So this may
  be long-standing and never actually verified working, not something
  broken by recent changes. Worth checking directly whether
  sd.OutputStream() ever raises/fails silently, and whether PortAudio's
  host API on the actual machine involved is one pavucontrol would even
  show at all.

- **GStreamer pipeline-restart timing may be worth a closer look
  separately:** in the captured log, trying encoder -> probe warning ->
  encoder selected -> pipeline PLAYING all completed within ~1.1s, right
  at the moment the AI demo logged "complete" -- consistent with
  set_mic_device's restore call (switching back from the sine tone to
  the original mic) not being awaited/confirmed before the demo
  considers itself done. Didn't get to fixing this directly this round.

- Confirmed AV source switching still explicitly out of scope, per
  Simleek's own prioritization.

- **Audio deep-dive round (both directions), based on Simleek's beep
  experiments -- which pinned down more than anything so far:**
  1. Brain-side playback (sounddevice): found the known failure mode
     matching every symptom. An exception escaping the OutputStream
     callback ABORTS the stream: sounddevice only prints to stderr
     (invisible under our logging) and the stream vanishes from
     pavucontrol -- permanent silence with a "started" log line and no
     other trace. Nothing detected this. Fixes: callback body fully
     guarded (logs + outputs silence instead of dying), a
     finished_callback that loudly WARNs whenever the stream stops for
     any reason, status flags at WARNING, a one-time INFO on the first
     real samples written (separates "callback never got data" from
     "playing but inaudible/misrouted"), producer-side flatten+float32
     (a (N,1) or float64 chunk would raise in the callback), and a
     leftover buffer so chunk/frame size mismatches never discard audio
     (opus decodes 960-sample chunks; blocksize=0 requests arbitrary
     frame counts -- the old callback threw every mismatch's tail away).
     This is NOT the OpenCV main-thread class of issue: PortAudio
     callbacks legitimately run on their own thread; the hazard is
     solely that errors there kill the stream silently.
  2. play_audio setting existed but was read by NOTHING. Now actually
     gates _start_audio.
  3. Endpoint-side playback (GStreamer): Simleek's endpoint beep test
     is the smoking gun -- ALSA 'default' silent, pipewire device
     audible. alsasink device='default' hits the same unrouted hole.
     _AudioRecvPipeline now uses autoaudiosink (which picks
     pipewiresink/pulsesink/alsasink, whichever actually works) when
     the device is 'default'/'auto'/empty; explicit strings (hw:X,Y)
     stay honored via alsasink exactly as before. Sink choice logged.
  4. DesktopHw now supports mic override too (arg -> mic_device
     setting -> auto-detect), same pattern as camera/speaker; the
     desktop mix stays first and therefore the default active input.
  5. New: tests/test_integration_scenarios.py -- full brain->wire->
     endpoint chains (real pack_obj/unpack_obj, real DesktopHw
     handlers, mocks only at pyautogui): an AI clicking an on-screen
     alert box it located by vision, an AI reacting to a 440Hz alarm
     it detected by FFT in the audio feed, human/AI handoff arriving
     correctly ordered at the endpoint, and a 12-point circle
     surviving the wire with exact coordinate fidelity. The first one
     would have caught the button-code mismatch automatically.

- **S16LE end-to-end completed (building on Simleek's commits), plus
  brain-side playback moved into GStreamer.** Simleek established that
  F32LE simply isn't supported by the actual audio hardware on most
  systems and standardized the GStreamer caps on S16LE; the remaining
  disagreeing layer was _pull_chunk, which still read the bytes as
  float32 -- S16=2 bytes/sample vs F32=4 explains the measured
  "almost exactly twice as much data". _pull_chunk now reads int16 and
  converts to float32 [-1,1] at the boundary, so every consumer
  (display waveform, AI, playback) keeps one consistent format. The
  sender's non-opus F32LE fallback is now S16LE unconditionally, and
  update_audio's None-before-dtype ordering bug is fixed.
  Brain-side playback: the sounddevice approach is dead on arrival
  with this display loop -- vsync blocking starves the realtime
  callback into constant clicks/underruns no matter the buffering
  (confirmed on hardware). GstReceiver now takes play_locally (wired
  from the play_audio setting): the receive pipeline tees after
  decode/convert/resample into the S16LE appsink branch (numpy,
  unchanged) and an audioconvert->autoaudiosink branch that negotiates
  its own format with the real hardware. Works headless. The
  sounddevice machinery in display_system stays present but unstarted
  until the GStreamer path is confirmed on hardware, then should be
  removed outright.
  Still open: sine tone works on the SECOND connection onward, not the
  first -- plausibly the set_mic_device restore-timing note from
  earlier, or initial autoaudiosink negotiation; retest after S16LE.
  The desktop-mix monitor loop carrying the sine back to the brain is
  intentional and useful (full round-trip health check), per Simleek.

- **Commit authorship corrected: earlier commits this branch were
  wrongly authored as Simleek** (Claude restored the wiped git config
  with the repo owner's identity after the environment reset). Config
  now set to Claude <noreply@anthropic.com> going forward; history not
  rewritten.

- **Post-music-milestone round (audio confirmed good on hardware:
  clicks gone, quality sufficient for detection/pitch/TTS work):**
  1. sounddevice playback fully removed from display_system per
     Simleek's finalize call -- the GStreamer play_locally tee is the
     confirmed path. Brain settings' speaker_device removed with it.
  2. Key hold watchdog (both sides). Lossy networks drop KeyEvents
     including releases -- observed live as F11 mashing until process
     kill. Brain re-sends key-down every 0.25s per held key
     (KEY_REFRESH_INTERVAL_S, desktop_system) tracked across BOTH input
     sources and updated even when transmit is gated (menu open /
     source switched mid-hold); endpoint auto-releases any held key not
     refreshed within 1.0s (KEY_WATCHDOG_TIMEOUT_S, desktop_hardware,
     daemon thread swept every 0.2s). Refresh key-downs don't re-press:
     already-held keys just restamp. Mouse buttons have the same
     theoretical stuck risk but no watchdog yet -- repeated mouseDown
     replay is less obviously safe than keyDown; revisit if observed.
  3. GStreamer congestion: sender video pipeline had NO queue and
     x264enc at defaults -- rc-lookahead 40+ frames plus B-frame
     reordering is over a second of buffering at 30fps before anything
     leaves the machine, matching "seconds-old frames at 2fps" on
     degraded wifi. Added a leaky=downstream max-size-buffers=1 queue
     before the encoder (stale frames drop at the SOURCE) and
     tune=zerolatency + speed-preset=ultrafast + key-int-max=2s for
     x264enc (zerolatency=true for nvenc where the property exists).
     Needs a degraded-network retest to confirm recovery behavior.
  4. Sine-on-first-connection: demo now waits a 2.0s settle after
     switching the sender to the test tone before counting duration,
     per the "stream takes a while to boot" read. If it still misses
     first connections, next suspect is the endpoint recv pipeline's
     own decoder-probe window.

- **AI viewport unification (the merge blocker, fixed).** Claude's
  earlier design rationale was exactly backwards: the zoom/pan/
  letterbox viewport exists FOR the AI -- it can only ingest small
  (~800x600-class) frames while the real screen is 1080p+, so without
  zoom/pan it literally cannot see enough detail to interact; the
  human edit-mode was the verification layer, not the point.
  send_frames_always already applied the shared viewport to the frame
  the AI receives, so bypassing inverse_map on the AI's mouse output
  meant what it SAW at (0.5, 0.5) was not where its click landed.
  Now: AI coordinates mean positions within the same canvas it sees
  and route through the same inverse mapping as human input
  (headless-safe via menu.out_res); the AI drives the viewport itself
  through new tokens (zoom in/out 1.1x, pan l/r/u/d 0.05, view reset)
  exactly like human edit mode. View controls gate on input_source
  since the viewport is currently SHARED with the human display --
  a per-consumer (separate AI) viewport is the eventual right shape
  if the AI should look around during human-driven sessions.

- **New branch (send_audio_array, off main) -- removed all test-only
  code from streamer_unencrypted.py.** AUDIO_SOURCE_SINE_TEST and its
  audiotestsrc branch had no business in a production file. Replaced
  with a genuinely general-purpose GstSender.play_array(samples,
  sample_rate) API: feeds an arbitrary numpy array into the send
  pipeline via appsrc (real-time-paced by a background thread -- a
  real speaker can't play faster than realtime either, and the
  receiver's low-latency queue isn't deep enough to absorb a whole
  clip arriving in a burst), reuses whichever encoder is already
  known-working rather than re-probing, and is fully self-contained:
  normal mic/desktop-mix audio resumes automatically once the array
  finishes, no caller-side save/restore needed (the old sine-test
  sentinel required exactly that dance). ai_passthrough_demo.py now
  builds its own numpy sine array locally and sends it through this,
  matching Simleek's exact ask -- "the numpy array itself should have
  a sine wave built into it, and that numpy array itself should play."
  Caught and fixed a real bug of my own while adding this: an earlier
  str_replace had accidentally merged the tail of _bind_input's body
  (the af_thru/af_edit human-input binding code) into the new
  _log_action_space_size method, silently breaking human input binding
  when _bind_input was called on its own. Found via the existing test
  suite, not observation -- exactly why the suite runs before every
  commit.

- **AI action-space size is now knowable.** There was no way at all to
  get the actual size of the AI's action space, which is mandatory for
  wiring up a real RL/neural-net-style AI. _log_action_space_size (in
  DesktopSubSystem.start()) now reports bound token count (13:
  6 mouse + 7 view control) and bound neuron count (2: mouse x/y).
  Keyboard keys are explicitly NOT included -- ai_key_press/release
  take an open string, not a fixed token index, so they aren't part of
  this enumerable space yet. Flagged rather than solved: a natural
  future mapping is one token per key with a threshold-gated neuron
  (0/1, -1/1, or a 0.5 crossing) instead of separate press/release
  tokens per key, but that's a bigger design decision than this round.

- **Continuous liveness diagnostics added to the demo** (not to the
  core AISubSystem/DesktopSubSystem classes -- would be spammy for a
  real production AI): _diagnostic_loop logs the HSV hue of in_img's
  center pixel and the FFT peak frequency of in_aud, once per second,
  for the demo's whole lifetime via async_loops. Cheap, human-checkable
  confirmation that video/audio are actually live and changing.

- **New: a real (non-mocked) GStreamer loopback integration test**
  (tests/test_gstreamer_loopback_integration.py). Sends a numpy sine
  array through the actual _AudioPipeline and receives it through the
  actual _AudioRecvPipeline over real UDP loopback -- no
  Gst.ElementFactory.make mocking, the genuine send/encode/RTP/decode/
  receive chain. Empirically calibrated before being committed: a
  1s/440Hz/amplitude-0.5 sine round-tripped with 99.7% of its spectral
  energy still concentrated within 40Hz of the target frequency, zero
  NaN, sample count within 1% of sent. Committed thresholds are
  deliberately looser than that measurement (ratio 0.6-1.6, spectral
  purity >0.85) to avoid flaking on a slower run while still
  meaningfully catching a genuinely broken chain. Confirms audiotestsrc/
  appsrc/appsink-based real GStreamer testing is fully viable in this
  sandbox with no virtual devices or real hardware needed -- worth
  extending to video if that becomes valuable.

- **Continuous audio streaming, exact key enumeration, real chunking
  validation, and initial stereo support -- all on send_audio_array.**
  1. Streaming: play_array only ever sent one fixed, finite array --
     can't send an infinitely long array for indefinite-length output
     (TTS, live relay, etc). Added GstSender.start_audio_stream() ->
     AudioStreamHandle: push() any number of chunks over time (0.1s-1s
     chunks work well per Simleek's own framing), end() when done. The
     SAME appsrc/encoder/RTP session stays alive across every pushed
     chunk -- no per-chunk pipeline rebuild, avoiding exactly the
     encoder-reset-artifact risk a naive "call play_array per chunk"
     approach would have. Self-contained like play_array: normal mic
     audio resumes automatically after end().
     Empirically validated Simleek's own click hypothesis directly: 5
     separately-pushed 200ms chunks of a 440Hz tone produced spectral
     purity identical to a single-array send (0.9970 both ways) and
     negligible energy above 2kHz (0.00004) -- clicks are broadband/
     square-wave-like, so this is a direct measurement of exactly the
     failure mode described. Committed as
     TestChunkedStreamingRoundTrip in the real (non-mocked) loopback
     integration file, with generously loosened thresholds from the
     calibration numbers.
  2. Key enumeration: added AI_SUPPORTED_KEYS to desktop_control_spec.py
     -- the actual, curated list (not just a count) of every key name
     keycode_to_pyautogui can produce: printable ASCII (32-126) plus
     the special-key table's values, deduplicated (the previous count
     formula double-counted punctuation overlapping both sets --
     genuinely 100 keys, not 130). Populates the keys_press/
     keys_release axes' previously-always-empty 'keys' field in
     RobotCapabilities, so a connecting brain gets the exact list
     automatically as part of the existing capabilities handshake --
     visible in the menu's capabilities preview too. Also reported
     directly in _log_action_space_size's startup log. Developers no
     longer have to guess whether e.g. f11 is actually reachable.
  3. Stereo/multi-channel: play_array/start_audio_stream now derive
     channels from the array's own shape (N,)=mono, (N,channels)=multi
     -- can't disagree with what was actually passed. Found and fixed a
     real bug while testing this: the RECEIVE side's caps still
     hardcoded channels=1 unconditionally, silently downmixing any
     stereo audio sent to it (caught by an empirical test showing
     received chunks came back as flat 1D instead of (N,2)).
     _AudioRecvPipeline now takes a channels param too; _process_sample
     reshapes to (N,channels) only when >1, so the mono default path
     (every existing consumer -- waveform display, AI's FFT) is
     completely unaffected. Empirically confirmed correct: two
     different frequencies sent on left/right arrived on the correct
     channel each, no swap, no bleed (TestStereoRoundTrip). This
     answers Simleek's own stated uncertainty ("hard to tell what
     gstreamer will actually support") -- it works, at the raw
     GStreamer pipeline level, in this sandbox.
     NOTE: channels is passed directly to _AudioRecvPipeline's
     constructor, bypassing the wire protocol entirely -- GstStreamInfo
     doesn't carry a channel count yet (mono was the only option end to
     end until now), so a receiving GstReceiver can't currently learn
     the sender's channel count automatically. Full negotiation (adding
     a channels field to GstStreamInfo's wire format) is a separate,
     larger task, not attempted this round. Also out of scope this
     round: brain-side consumption of multi-channel audio beyond the
     raw pipeline (AISubSystem/DisplaySubSystem's update_audio, the
     waveform display, and the diagnostic FFT print all still assume
     1D mono -- reshaping only kicks in when channels>1 is explicitly
     requested at the pipeline level, so nothing existing broke, but
     nothing upstream of the pipeline understands stereo yet either).

- **Brain-side stereo consumption (display + AI), and the streaming
  API demoed on the endpoint side.**
  1. DisplaySubSystem's waveform square: mono stays exactly as before
     (2D grayscale). Stereo (N,2) now builds a genuine (rows,cols,3)
     image -- channel 0=left, 1=right, 2=zeros, since 2-channel images
     are awkward to display (RGB/RGBA is the standard, not 2) and the
     unused third channel doesn't need an invented meaning.
  2. AISubSystem.in_aud is now explicitly documented as mono (N,) or
     stereo (N,2) -- it already passed either through unchanged, the
     gap was purely that this wasn't discoverable without reading the
     pipeline code.
  3. Found and fixed a latent bug while touching this: the demo's
     _diagnostic_loop ran rfft directly on in_aud, which for a 2D
     stereo array operates along the wrong axis (channels, not time) --
     silently meaningless output rather than an error. Now mixes down
     to mono first.
  4. examples/desktop/desktop_endpoint.py: added audio_stream_demo,
     using GstSender.start_audio_stream()/push()/end() from the
     endpoint side -- waits for a brain connection, streams a test tone
     in chunks once, so Simleek can verify the streaming API on real
     hardware directly, not just the sandboxed loopback tests.

- **Real mistake this round: an accidental `cp -a` in the wrong
  direction (pristine -> working copy) during a diagnostic A/B test
  overwrote several files' uncommitted changes from this same round**
  (display_system.py, ai_system.py, ai_passthrough_demo.py,
  desktop_endpoint.py, plus test additions to three existing files).
  Caught immediately by checking known markers post-overwrite; all
  lost work was still fresh in context and got re-applied verbatim,
  confirmed via test count matching (506) and a second clean run. Only
  a genuinely new file (test_desktop_endpoint_audio_stream_demo.py)
  survived on its own, since cp -a doesn't delete files absent from
  the source -- it only overwrites/adds shared filenames. Lesson: never
  sync pristine -> working copy mid-round; only sync working copy ->
  pristine, and only right before committing.

- **Confirmed pre-existing, unrelated to this round: a test-isolation
  failure** (test_menu_shutdown_and_ui.TestSelectionMenuLocalhostGating
  .test_enter_on_enabled_local_calls_switch_mode) that passes cleanly
  alone but fails when run as part of the full suite ("no current
  event loop in thread MainThread") -- reproduced identically on the
  pre-this-round commit too (491 tests, same single failure), so some
  other test earlier in suite order is leaving the default event loop
  in a bad state for asyncio.ensure_future's implicit get_event_loop()
  call. Not investigated further this round; worth a dedicated look.

- **Aligned with Simleek's own commit (88d5e5c) and fixed a bug it
  introduced, then finished the three remaining asks.**
  1. Found a real bug in Simleek's own rename: reshape_to_square_matrix
     / reshape_stereo_to_square_image were unified into a single
     reshape_to_square_image(arr, pad_to_rgb=True), a genuinely better
     design (generalizes to any channel count; mono correctly stays
     plain 2D since pad_to_rgb only triggers when a trailing channel
     axis with <3 channels already exists) -- but run_once's call site
     wasn't updated, still referencing both now-nonexistent old names.
     Would have raised NameError the moment any real audio arrived.
     Fixed: run_once now just calls reshape_to_square_image(aud)
     unconditionally: the unified function already handles both cases.
  2. Updated tests to match: the two renamed/unified test classes, the
     diagnostic loop's new plural "peak audio frequencies" log label
     (per-channel FFT via axis=0, better than my own earlier mixdown --
     preserves per-channel info instead of discarding it). Removed
     tests/test_desktop_endpoint_audio_stream_demo.py entirely -- its
     target function no longer exists there, correctly, per point 4.
  3. Desktop mix now defaults to stereo (music/video on the desktop is
     typically stereo) with automatic fallback to mono if 2 channels
     genuinely can't be negotiated (probe() failure) -- logged as an
     error on fallback. Everything else (a specific mic device,
     brain-side sending via play_array/start_audio_stream) stays mono
     by default, unchanged.
  4. Added _stream_audio_demo to AiPassthroughDemo (brain side) --
     GstSender.start_audio_stream()/push()/end(), running after
     _play_sine_tone completes, at 880Hz (an octave up, distinguishable
     by ear from _play_sine_tone's 440Hz). This replaces the version I'd
     put on the endpoint side, which Simleek correctly removed -- demo/
     test functionality doesn't belong in a production-facing example
     script; it belongs with the other AiPassthroughDemo demonstrations.

- **Answered empirically rather than by reasoning about GStreamer
  negotiation abstractly: does a mono mic actually combine into a
  stereo desktop mix, or get dropped/force it down to mono?** Built a
  real GStreamer pipeline matching _build_desktop_mix_source's exact
  branch structure (mono source + stereo source, each through their own
  audioconvert/audioresample/queue, both into one audiomixer, forced to
  channels=2 downstream) with audiotestsrc standing in for pulsesrc
  (which is too tightly coupled to a real 'device' property to
  substitute directly). Result: audiomixer correctly upmixes the mono
  source to match the negotiated stereo output -- its tone showed up
  with strong energy on BOTH channels, while the stereo source's own
  panning survived the mix intact (dominant on the channel it was
  panned to). So the existing _build_desktop_mix_source code should
  already handle mono-mic + stereo-desktop correctly as-is, now that
  desktop mix defaults to requesting channels=2 downstream (the thing
  that drives audiomixer's negotiation toward stereo in the first
  place) -- no code change needed there, just confirmed with a real,
  committed test (TestMonoMicCombinesIntoStereoDesktopMix) rather than
  left as an assumption.

- **Found and fixed the actual bug Simleek verified on real hardware:
  endpoint correctly sent 2-channel desktop mix, but the brain received
  it as mono.** Root cause was exactly what Simleek called out:
  channels was added to _AudioRecvPipeline directly and tested there in
  isolation, but never threaded through GstReceiver (which didn't
  accept a channels parameter at all) or into MenuSubSystem's
  construction of it -- so AiPassthroughDemo and robonet.brain.main
  (both go through a bare MenuSubSystem()) always got
  _AudioRecvPipeline's raw class default (1) regardless of what
  actually arrived over the wire. The low-level mechanism was correct;
  the wiring to reach it in production was simply never built.
  Fixed the full chain: new receive_channels setting (default 2,
  matching the endpoint's own desktop-mix default) -> MenuSubSystem
  passes it to GstReceiver(channels=...) -> GstReceiver threads it into
  _build_audio_pipeline's _AudioRecvPipeline(channels=...) call. Also
  added the channel-count logging Simleek specifically noted was
  missing on the receive side ("(2ch)" now printed alongside both
  "trying audio decoder" and "audio decoder selected", matching the
  send side's existing pattern).
  Several test fixtures (fake settings dicts, a fake GstReceiver stand-
  in) needed the new key/attribute added -- same class of gap as every
  previous settings addition this session. Added
  TestGstReceiverChannelsWiring and two MenuSubSystem-level tests
  specifically covering the layer that was actually missing (settings
  default reaching the real GstReceiver instance), not just the
  low-level pipeline mechanism already covered by last round's
  loopback tests.
