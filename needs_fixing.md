# Needs Fixing

Found while implementing desktop-mode, wired pairing, auto-connect, and
auto-shutdown. Not fixed (per instruction) unless noted otherwise. No
claim these are all correct diagnoses -- flagged for a second look, not
filed as confirmed bugs.

## Likely real bugs

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

- **robonet/adhoc_pair/server.py, set_hotspot()**: `nmcli con modify
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

## Fixed opportunistically (blocked the requested auto-shutdown feature)

- **robonet/brain/menu_system.py, MenuSubSystem.timeout_loop()**:
  referenced `self._endpoints`, which was never set anywhere in
  `MenuSubSystem.__init__` (only `SelectionMenu` has that attribute) --
  would have raised AttributeError the first time the loop actually ran
  past the `_connected` check. Had to touch this function anyway to
  build the requested auto-shutdown-with-callbacks feature, so fixed it
  in place rather than filing it here and building the new feature on
  top of a call that would crash.

## Code smells / things that look intentional but are worth a second look

- ~~**robonet/brain/main_system.py + desktop_window_config.py**: `.actions`
  vestigial code~~ -- **confirmed and removed.** Simleek checked with an
  IDE-wide search: `pass_through_cb`/`edit_cb` close over `af_thru`/
  `af_edit` directly and are what's actually used; `sm.actions` (both the
  `ServerSystem.__init__` instance and the two assignments in
  `desktop_window_config.py`, one of which always overwrote the other
  anyway) had no reads anywhere. Removed all three.

- ~~**robonet/util.py**: `send_burst`/`receive_burst` + `PlainRadioEngine`
  duplicate `SecureRadioEngine`~~ -- **confirmed dead and moved.**
  Simleek: "I was expecting the lack of encryption to speed things up,
  but in practice it didn't, and using gstreamer made things much
  faster." Grepped the whole repo -- genuinely unreferenced anywhere in
  the active `RobotRadio`/`RadioSubSystem` path. Moved to
  `todo/plain_radio.py` (along with `receive_objs`/
  `unwrap_topic_from_plain_packet` from `receive_callbacks.py`, now
  `todo/plain_receive_callbacks.py`, and `tests/run_fft_understanding.py`,
  the only thing that used them, now `todo/run_fft_understanding.py`).
  See `todo/TODO.md`.

- ~~**Non-ASCII characters scattered through existing comments/UI
  strings**~~ -- **confirmed, swept this pass.** Em dashes and arrows
  throughout the pre-existing codebase (e.g. selection_menu.py's UI
  glyphs, doc comment arrows, one checkmark emoji in
  examples/setup_vnc_client.sh). Simleek: "those are all bad and should
  be removed and replaced with equivalent ascii." See the ASCII sweep
  section further down for what was actually touched.

## Fixed after initial review (flagged by Simleek)

- **wired_pair had no client-side.** Initial version only configured the
  brain's own interface, reasoning (documented in the module's own
  docstring at the time) that a direct cable has no server/client role
  asymmetry so one function could serve both ends. That missed the actual
  point: the *brain* configuring *its own* interface doesn't put any IP
  on the *endpoint's* interface -- RobotRadio's DISH socket binding to
  0.0.0.0 still needs the OS to have an address on that interface at all
  to route packets to it. Fixed by adding `robonet/wired_pair/client.py`
  (`connect_wired()`), reusing the same `set_wired_static()` the brain
  side already uses, plus matching `wired_endpoint_ip`/`wired_subnet`
  settings on the endpoint side.

- **Settings existed but nothing in the actual receiving code used
  them.** Follow-up catch on the fix above: `wired_endpoint_ip`/
  `wired_subnet` were readable from `endpoint/settings.py` and consumed
  by the standalone `wired_pair/client.py` script, but
  `robonet/endpoint/radio_system.py` -- `RobotRadio`, the class that
  actually owns the receiving DISH socket -- had zero "wired" references
  at all (checked with `grep -rn wired robonet/endpoint/`). Fixed:
  `RobotRadio.__init__` now calls `connect_wired()` itself, gated behind
  a new opt-in `auto_wired_setup` setting (default `False`) and wrapped
  so failure (no cable, no nmcli, whatever) just logs and continues --
  running this unconditionally on every endpoint startup would reconfigure
  the first ethernet interface with a cable plugged in via nmcli, which
  could just as easily be someone's normal wired internet connection as
  one intended for robonet pairing, so it stays opt-in rather than
  automatic-by-default.

- **Deleted `robonet/local_wifi_pair/`, `robonet/localhost_pair/`, and
  `robonet/adhoc_pair/client.py`** -- confirmed via grep that nothing
  outside their own directories imported any of them (all three were
  standalone scripts on an older `client_unicast_communication`/
  `client_udp_discovery` pattern, predating RobotRadio/RobotNode
  entirely). Still in git history if needed later. Kept
  `adhoc_pair/server.py`, since `robonet/brain/radio_system.py`'s ADHOC
  mode genuinely still calls `set_hotspot()`/`lazy_pirate_send_con_info()`
  from it -- that one's real production code, not a leftover test.

## Fixed (confirmed real, per direct request)

- **`tests/test_buffers.py` -- fully fixed, not just noted.** Two
  separate stale-test bugs, both from the test file drifting out of
  sync with the actual buffer classes rather than anything wrong with
  the classes themselves: (1) imported/constructed `CamFrame`, which
  doesn't exist -- the actual class is `CVCamFrame`; renamed. (2)
  `AudioBuffer(audio_data=..., sample_rate=...)` -- `AudioBuffer`'s real
  fields are `fft_data`/`sample_rate`/`samples_per_sec`, not
  `audio_data`; fixed the keyword and the test data's dtype
  (`np.complex128`, matching the field's actual codec).

- **`AudioBuffer.fft_data`'s declared type hint doesn't match its
  codec.** `type_list = [List[npt.NDArray[np.complex64]], ...]` says
  complex64, but the codec is `_c128_arr_list = ndarray_list_codec(np.complex128)`
  -- complex128. Runtime behavior follows the codec (confirmed while
  fixing the test above -- complex64 data fails an internal dtype
  assert, complex128 doesn't), so the type hint is just wrong/stale, not
  currently causing incorrect behavior. Left the class alone; noting it
  since I don't know which precision was actually intended.

## Found and fixed via testing (this session, own new code)

- **`_PYAUTOGUI_IMPORT_ERROR`/`_SOUNDDEVICE_IMPORT_ERROR` had no default.**
  Both `desktop_hardware.py` and the new `ai_audio.py` had the same
  shape: `try: import X / except Exception as _e: X = None;
  _X_IMPORT_ERROR = _e`. Since the import actually succeeds in any
  normal environment, `_X_IMPORT_ERROR` is only ever defined inside the
  except branch -- so the error-message code path
  (`f'...{_X_IMPORT_ERROR}...'`) hit `NameError` instead of the intended
  clear error, in any situation where the module-level name got set to
  None by something other than that exact except block running.
  `test_ai_audio.py`'s `test_raises_clear_error_when_sounddevice_unavailable`
  (patches only `sounddevice`, not the error var) caught this directly.
  The equivalent `desktop_hardware.py` test had accidentally also
  patched `_PYAUTOGUI_IMPORT_ERROR` into existence, masking the same
  bug there -- added a second test that patches only `pyautogui` to
  catch the regression. Fixed both by giving the `_X_IMPORT_ERROR` name
  a `None` default before the try block.

## Deferred (thought through, not implemented -- see reasoning below)


- **examples/setup_vnc_client.sh**: explicitly marked "untested
  boilerplate, probably doesn't work" and references a `robopi_client` /
  `robotar.vnc_client` module that doesn't exist anywhere in the current
  tree. Looks like leftover scaffolding from an earlier naming scheme
  (robotar vs robonet).

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
  offset instead of a single fixed port per ip. Not done here because it
  also touches `_probed_ips` (currently a set of bare ips, would need to
  become ip:port pairs), `_disconnect_all`, `connect_to`, `_on_scanner_lost`,
  and `NetworkScanner._probe_port`'s TCP liveness check -- real surface
  area across both sides' core discovery path, and not something I could
  verify here without two real machines (or at least two real processes
  actually exchanging UDP over a real interface, not just importable
  Python). Given the instruction to prioritize verified work this session,
  writing up the design seemed better than shipping an unverified change
  to the one thing that already reliably works (discovery).

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
  behavior.

- **Claude Code CLI wrapper**: resolved outside this repo -- Simleek has
  already built this separately; it depends on the fixed IPs this
  session's wired-mode work provides and is otherwise complete.

- **Audio neurons/blocksize -- now implemented.** Original idea was
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
  as the way to check the real device name on an actual machine if it
  doesn't resolve.
