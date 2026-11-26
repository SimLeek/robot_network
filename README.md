# robot_network (robonet)

A general robot controller and remote desktop system. Works over Wi-Fi,
ethernet, and localhost, and has encrypted and unencrypted communication options.

## Installation

```
git clone https://github.com/SimLeek/robot_network.git
cd robot_network
./install.sh
source venv/bin/activate
```

### Ethernet

Ethernet requires some special setup.

On the brain or server side:

  ```
  python -m examples.setup_eth_server
  ```

On the endpoint side:

  ```
  python -m examples.setup_eth_client
  ```

## Running -- brain side

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

### AI Control

The brain hosts a shared-memory bridge (`robonet/bridge/`) that a separate
AI process attaches to. Run the brain headless and point it at the
endpoint on the same machine:

```
# ~/.robobrain/settings.json: {"auto_connect_priority": ["localhost"]}
python -m examples.desktop.desktop_endpoint                     # on the X display
python -m robonet.brain.main --headless --monitor-dir ~/robonet_monitor
python -m examples.bridge_ai_smoke                              # or your AI
```

An AI subclasses `RobonetAIClient` (`robonet/bridge/ai_client_base.py`):
frames arrive in `on_channel_data` (shape/dtype in `self.channel_info`),
and `set_want_control('ai')` + `send_key` / `send_mouse_move` /
`send_mouse_button` / `send_scroll` / `send_token` drive the endpoint.
Control returns to the human (and held keys are released) if the AI
stops sending heartbeats for 5 s.

`--monitor-dir` writes `frame.png` (what the AI sees), `status.json`
and `status.jsonl` every `--monitor-interval` seconds, for watching a
headless run over ssh.

If the desktop audio arrives silent, the default source (mic) may be
delivering nothing and starving the mixer: set
`"desktop_audio_include_mic": false` in `~/.robotar/settings.json` to
send the default sink's monitor alone.

### Blocked keys and the kill switch

The endpoint refuses key presses that complete a combo listed in
`~/.robotar/settings.json`, e.g.

```
{"blocked_key_combos": [["f11"], ["alt", "f4"], ["ctrl", "alt", "f2"]]}
```

`python -m robonet.killswitch` (in the endpoint's X session) stops every
robot_network python process when Ctrl+Alt+Shift+F12 is pressed on the
physical keyboard, then releases all keys and mouse buttons. The endpoint
always blocks that combo, so nothing sent through robonet can trigger it.
`python -m robonet.killswitch --now` does the same from a shell (e.g. ssh).

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

## Shared secret (PSK) setup

generate 2 PSKs on either machine, then copy both files to both machines:

```
python robonet/gen_psk.py   # writes ./psk.key
```

Rename as needed and place them at:

- Brain: `~/.robobrain/psk.key` and `~/.robobrain/server_psk.key`
- Endpoint: `~/.robotar/psk.key` and `~/.robotar/server_psk.key`
