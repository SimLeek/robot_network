"""Server communicating through adhoc UDP after disconnecting from the local Wi-Fi."""

import zmq
import subprocess

from robonet.buffers.buffer_objects import WifiSetupInfo
from robonet.buffers.buffer_handling import pack_obj
from robonet.util import get_local_ip, switch_connections, get_connection_info, server_udp_discovery, \
    server_unicast_communication


def _lazy_pirate_send_con_info(ctx, obj, local_ip):
    """Lazy pirate server pattern sending direct connection info."""
    server = ctx.socket(zmq.REP)
    server.bind(f"tcp://{local_ip}:9998")

    _ = server.recv()
    response = pack_obj(obj)
    server.send(response)

    server.close()


def _set_hotspot(wifi_obj: WifiSetupInfo, devices):
    """Set up an adhoc_pair hotspot that matches the wifi_obj."""

    try:
        result = subprocess.run(
            "nmcli -t -f connection.id con show {wifi_obj.ssid}", shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as _:
        result = None  # connection doesn't exist returns exit code 10

    prefix = 24

    try:
        if result is not None and result.stdout:
            commands = [f"nmcli con delete {wifi_obj.ssid}"]
        else:
            commands = []
        commands.extend([
            f"nmcli con add type wifi ifname {devices[0]} con-name {wifi_obj.ssid} autoconnect yes ssid {wifi_obj.ssid}",
            f"nmcli con modify {wifi_obj.ssid} 802-11-wireless.mode adhoc_pair ipv4.addresses {wifi_obj.server_ip}/{prefix} ipv4.method manual ipv6.method ignore",
            f"nmcli con up {wifi_obj.ssid}",
        ])
        for command in commands:
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Setting wifi to access point failed: {e.stderr}")


def run(callback_loop):
    """Main function to run the server."""
    ctx = zmq.Context.instance()

    local_ip = get_local_ip()
    _ = server_udp_discovery(ctx, local_ip)

    wifi_obj = WifiSetupInfo("robot_wifi", "192.168.2.1", "192.168.2.2")
    _lazy_pirate_send_con_info(ctx, wifi_obj, local_ip)

    devices, current_connection = get_connection_info()
    try:
        _set_hotspot(wifi_obj, devices)
        server_unicast_communication(ctx, wifi_obj.server_ip, wifi_obj.client_ip, callback_loop)
    finally:
        switch_connections(wifi_obj.ssid, current_connection)
        ctx.term()


if __name__ == "__main__":
    from robonet.server_callbacks import display_mjpg_cv

    run(display_mjpg_cv)
