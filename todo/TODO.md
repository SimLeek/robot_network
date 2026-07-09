# todo/

Code that's confirmed unreferenced by the active `RobotRadio`/
`RadioSubSystem` path (verified by grepping the whole repo before moving
anything here, not just assumed), parked here instead of deleted since
some of it might be worth reviving. Nothing in `robonet/` or `examples/`
imports anything from `todo/` -- if that ever stops being true, something
here needs to move back.

## Why this exists

Per Simleek: "I was expecting the lack of encryption to speed things up,
but in practice it didn't, and using gstreamer made things much faster."
The unencrypted/plain-radio path was an earlier attempt at performance
that didn't pan out; GStreamer's own transport turned out to be the
actual speedup. The encrypted GStreamer path was never finished/used
either. Both are kept here for reference rather than deleted outright.

## What's here

- **`gst_io/streamer_encrypted.py`, `gst_io/receiver_encrypted.py`** --
  the SRTP/encrypted GStreamer sender and receiver. Never wired up
  anywhere active; `streamer_unencrypted.py`/`receiver_unencrypted.py`
  (the ones actually used) are described in their own docstrings as
  "drop-in replacements" for these.

- **`plain_radio.py`** -- the unencrypted counterpart to
  `robonet/util.py`'s `SecureRadioEngine`: `send_burst`, `receive_burst`,
  `HEADER_FMT`/`HEADER_SIZE`, `make_plain_topic_block`/
  `wrap_packet_with_plain_topic`, `PlainRadioEngine`. Matches
  `SecureRadioEngine`'s public API exactly (by design, so the two were
  meant to be swappable), confirmed unreferenced anywhere active.

- **`plain_receive_callbacks.py`** -- the unencrypted counterpart to
  `robonet/receive_callbacks.py`'s `receive_objs_encrypted`:
  `unwrap_topic_from_plain_packet`, `receive_objs`. Only ever used by
  `run_fft_understanding.py` (also moved here, see below), imports
  `PlainRadioEngine` from `plain_radio.py`.

- **`run_fft_understanding.py`** -- a standalone exploration script
  (moved from `tests/`, since it isn't a test in the assertions sense and
  is built entirely around the now-relocated plain-radio pattern). Its
  `receive_objs` import was updated to point here.

## `_step_bitrate` (not moved, just removed)

`streamer_unencrypted.py` had a dead, unused copy of `_step_bitrate`
(RTCP-loss-driven adaptive bitrate stepping) plus a cluster of constants
only that method referenced (`BITRATE_MIN`, `BITRATE_MAX`,
`BITRATE_STEP_DN`, `BITRATE_STEP_UP`, `RTCP_LOSS_THRESHOLD`,
`RTCP_DROP_COUNT`, `RTCP_GOOD_COUNT`) -- confirmed via grep that none of
them were referenced anywhere else in that file. Removed outright rather
than moved, since the *real*, actually-referenced version already lives
in `gst_io/streamer_encrypted.py` above (called from its RTCP feedback
handling), which is preserved here in full. If adaptive bitrate is
wanted for the currently-active unencrypted path, it needs to be built
fresh against that file's RTCP feedback loop -- `streamer_unencrypted.py`
doesn't have one to hook `_step_bitrate` into, which is presumably why
the copy sat there dead.

## Possibly also legacy, not yet investigated

Noticed but out of scope for this pass: `robonet/server.py` and
`robonet/client_basic_example.py` look like they may be similar-vintage
standalone scripts to the three pairing packages that were deleted
(`local_wifi_pair`, `localhost_pair`, `adhoc_pair/client.py`) -- e.g.
`robonet/server.py` defines its own `get_local_ip()` rather than
importing the one in `robonet/util.py`. Not confirmed dead or acted on;
flagging for a future look.
