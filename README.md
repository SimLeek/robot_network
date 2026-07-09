# robot_network (robonet)

Remote control and sensor streaming between a "brain" (the machine a
human or AI operates from) and one or more "endpoints" (a robot, or a
desktop machine being remote-controlled), over GStreamer for audio/video
and an encrypted UDP radio for control/telemetry.

## Installation

```
git clone https://github.com/SimLeek/robot_network.git
cd robot_network
./install.sh
```

`install.sh` runs three scripts in order:

1. **`network_setup.sh`** -- installs GStreamer and its plugins, opens
   the firewall ports this project uses (9998/9999 for the control
   radio, 5600-5603 for GStreamer's RTP streams).
2. **`pyzmq_setup.sh`** -- builds `pyzmq` from source with ZeroMQ's
   draft API enabled. This is required, not optional: the control radio
   uses `zmq.DISH`/`zmq.RADIO` sockets, which only exist in draft-API
   builds. A plain `pip install pyzmq` will not have these and things
   will fail with a confusing "no such socket type" style error.
3. **`pip_install_this_package.sh`** -- creates (or reuses) a `./venv`
   virtual environment with access to system site-packages (needed for
   `PyGObject`/GStreamer's Python bindings, which are usually installed
   at the system level, not via pip), then `pip install -e .`.

Activate that virtual environment (`source venv/bin/activate`) before
running anything below, in every new shell.

## Running -- brain side

The brain is the machine a human (or eventually an AI) operates from.
Run it as a module, from the repo root:

```
python -m robonet.brain.main
```

This opens a human-facing window (via `displayarray`) showing whatever
endpoint you connect to, with an on-screen menu (default toggle:
Ctrl+`` ` ``) for picking a network mode and endpoint. Settings live at
`~/.robobrain/settings.json` (see `robonet/brain/settings.py` for
every key and its default).

## Running -- endpoint side

Endpoint scripts can be run either as a module or by direct file path --
both work the same way (each script fixes up `sys.path` itself so its
`robonet` imports resolve either way):

```
python -m examples.desktop.desktop_endpoint
# or, equivalently:
python examples/desktop/desktop_endpoint.py
```

```
python -m examples.basicpibot.robot_endpoint
# or, equivalently:
python examples/basicpibot/robot_endpoint.py
```

Settings live at `~/.robotar/settings.json` (see
`robonet/endpoint/settings.py`).

### One-time setup, depending on how you're connecting

These only need to be run once per machine (or again after certain
system changes -- see each script's own docstring/comments for specifics):

- **Desktop-mode capture** (before the first run of
  `desktop_endpoint.py` on a given machine): loads the `v4l2loopback`
  and `snd-aloop` kernel modules used to expose the desktop's
  screen/audio as a normal-looking webcam and microphone. Needs sudo.
  ```
  ./examples/setup_desktop_capture.sh
  ```

- **Wired (direct ethernet cable) connections** (after plugging the
  cable in, before starting the endpoint script -- or set
  `auto_wired_setup: true` in `~/.robotar/settings.json` to have the
  endpoint do this automatically on every startup instead):
  ```
  python -m examples.setup_eth_client
  ```

Wifi and localhost connections need no extra one-time setup on the
endpoint side.
