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
source venv/bin/activate
```

`install.sh` installs GStreamer and its plugins and then creates (or reuses) 
   a `./venv` virtual environment with access to system site-packages
   (needed for `PyGObject`/GStreamer's Python bindings

Activate that virtual environment (`source venv/bin/activate`) before
running anything below, in every new shell. Re-run `install.sh` (or
`pip install -e .` in the venv) after pulling if dependencies changed.

## Running -- brain side

The brain is the machine a human or AI operates from.

### Human Control
For human control, run it as a module, from the repo root:

```
python -m robonet.brain.main
```

This opens a human-facing window showing whatever
endpoint you connect to, with an on-screen menu (default toggle:
Ctrl+`` ` ``) for picking a network mode and endpoint. Settings live at
`~/.robobrain/settings.json` (see `robonet/brain/settings.py` for
every key and its default).

### One-time Brain Setup

- **Wired (direct ethernet cable) connections**
  ```
  python -m examples.setup_eth_server
  ```
  If this fails because the interface is already a bridge/bond port,
  use a different interface or configure the bridge itself instead.

### AI Control

A default AI interface hasn't been created yet. 
It would require modifying main, 
and then would need to be modified for any specific AIs

## Running -- endpoint side

The examples directory has several fully implemented example endpoints:

```
python -m examples.desktop.desktop_endpoint
```
```
python -m examples.basicpibot.robot_endpoint
```

You can either run these as is or modify them for your specific endpoint.

Endpoint settings live at `~/.robotar/settings.json` (see
`robonet/endpoint/settings.py`).

### One-time Endpoint Setup

These usually only need to be run once per machine:

- **Desktop-mode capture**
  ```
  ./examples/setup_desktop_capture.sh
  ```

- **Wired (direct ethernet cable) connections**
  ```
  python -m examples.setup_eth_client
  ```

## Shared secret (PSK) setup

generate 2 PSKs on either machine, then copy both files to both machines:

```
python robonet/gen_psk.py   # writes ./psk.key
```

Rename as needed and place them at:

- Brain: `~/.robobrain/psk.key` and `~/.robobrain/server_psk.key`
- Endpoint: `~/.robotar/psk.key` and `~/.robotar/server_psk.key`
