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

- **robonet/brain/main_system.py + desktop_window_config.py**:
  `make_window_config_for_server(sm, af_thru, af_edit)` does `sm.actions
  = af_thru` and then, a few lines later, `sm.actions = af_edit` -- the
  second assignment always wins, so `sm.actions` ends up permanently
  `af_edit` regardless of which factory was actually newly created.
  Separately, `ServerSystem.__init__` also sets `self.actions =
  ActionFactory()` (a third, distinct instance) which doesn't appear to
  be read anywhere. `sm.actions` may just be vestigial.

- **robonet/util.py**: `send_burst`/`receive_burst` (module-level plain
  functions, unencrypted) and `SecureRadioEngine.send_burst`/
  `process_raw_packet` (class methods, AES-GCM encrypted) implement very
  similar burst-assembly logic twice, with the plain version's docstring
  noting "use this only for internal communication, such as wired or
  radio within a faraday cage." Only `SecureRadioEngine` is actually
  wired up anywhere currently (both endpoint and brain always use the
  encrypted path, including in LOCALHOST mode). Worth deciding
  explicitly whether the plain path is meant to be reachable somewhere
  (e.g. for wired mode specifically, given the docstring) or is dead
  duplicate code to remove.

- **robonet/adhoc_pair, local_wifi_pair, localhost_pair**: three
  standalone `client.py`/`server.py` pairs with their own
  `if __name__ == '__main__':` entry points, predating the current
  `RadioSubSystem`/`RobotNode` architecture. `radio_system.py` still
  imports `set_hotspot`/`lazy_pirate_send_con_info` from
  `adhoc_pair/server.py` for real, but `local_wifi_pair` and
  `localhost_pair` look fully orphaned relative to the current brain
  code path -- nothing in `robonet/brain/` or `robonet/endpoint/`
  references them. Might be worth archiving or deleting if confirmed
  unused, to stop them drifting further from the real handshake.

- **Non-ASCII characters scattered through existing comments/UI
  strings**: em dashes and arrows appear throughout the pre-existing
  codebase (e.g. selection_menu.py's UI glyphs `-> . |`, doc comment
  arrows, one checkmark emoji in examples/setup_vnc_client.sh). Given
  the standing ASCII-only rule, these are pre-existing debt worth a
  cleanup pass at some point. Left alone except in the exact lines
  touched for this work (a couple of section-header comments in
  buffer_objects.py that were being edited anyway).

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

- **"Speaker out from neurons out tensors, mic in same as camera."**
  Read this as: eventually let an AI's raw neuron-output tensor drive
  audio *output* (e.g. synthesized speech/tone) the same way
  `SparseVectorBuffer` drives a robot's motors today, and treat mic input
  as just another received stream on the same footing as camera video
  (which, worth noting, it already mostly is -- `CamMicSpkRobotHardware`
  already pairs mic with camera symmetrically). The audio-output-from-
  neurons half doesn't have a concrete spec yet (what tensor shape, what
  synthesis step turns it into a waveform), so there's nothing to build
  against yet -- flagging for a follow-up conversation rather than
  guessing at a design.

- **Claude Code CLI wrapper over robonet's IP/connection layer**, chat-only
  (no code-tool access), prefixing desktop-originated messages with
  `P:`. Genuinely a separate piece of software from this repo, and
  underspecified enough (which "P", what the wrapper actually talks to on
  the robonet side, whether it lives in this repo at all) that building it
  now risks guessing wrong. Wanted to flag clearly that this was read and
  not silently dropped, rather than either ignoring it or building
  something off a half-guess.
