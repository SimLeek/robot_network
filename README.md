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

## Shared secret (PSK) setup

generate 2 PSKs on either machine, then copy both files to both machines:

```
python robonet/gen_psk.py   # writes ./psk.key
```

Rename as needed and place them at:

- Brain: `~/.robobrain/psk.key` and `~/.robobrain/server_psk.key`
- Endpoint: `~/.robotar/psk.key` and `~/.robotar/server_psk.key`
