"""Searches for a server, receives ad hoc connection info, then switches to the ad hoc connection as robot"""

import zmq
import subprocess

from robonet.buffers.buffer_handling import unpack_obj
from robonet.util import get_local_ip, switch_connections, get_connection_info, client_unicast_communication, client_udp_discovery


def lazy_pirate_recv_con_info(ctx, server_ip, timeout=2500, retries=10):
    """Lazy pirate client pattern requesting direct connection info."""
    client = ctx.socket(zmq.REQ)
    client.connect(f"tcp://{server_ip}:9998")

    while True:
        client.send(b'pls')

        retries_left = retries
        while True:
            if (client.poll(timeout) & zmq.POLLIN) != 0:
                reply = client.recv()
                reply_obj = unpack_obj(reply)
                client.close()
                return reply_obj

            retries_left -= 1
            # Socket is confused. Close and remove it.
            client.setsockopt(zmq.LINGER, 0)
            client.close()
            if retries_left == 0:
                raise SystemError("Server seems to be offline, abandoning")

            # Create new connection
            client = ctx.socket(zmq.REQ)
            client.connect(f"tcp://{server_ip}:9998")
            client.send(b'pls')

def connect_hotspot(wifi_obj, devices):
    try:
        command = f"nmcli connection show | grep {wifi_obj.ssid}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as _:
        result = None  # empty grep

    prefix = 24

    try:
        if result is not None and result.stdout:
            commands = [f"nmcli con delete {wifi_obj.ssid}"]
        else:
            commands = []
        commands.extend([
            f"nmcli con add type wifi ifname {devices[0]} con-name {wifi_obj.ssid} autoconnect yes ssid {wifi_obj.ssid}",
            f"nmcli con modify {wifi_obj.ssid} 802-11-wireless.mode adhoc ipv4.addresses {wifi_obj.client_ip}/{prefix} ipv4.method manual ipv6.method ignore",
            f"nmcli con up {wifi_obj.ssid}"
        ])
        for command in commands:
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Connecting to the robot ad hoc server failed: {e.stderr}")

def run(callback_loop):
    """Main function to run the client."""
    ctx = zmq.Context.instance()

    local_ip = get_local_ip()
    server_ip = client_udp_discovery(ctx, local_ip)

    wifi_obj = lazy_pirate_recv_con_info(ctx, server_ip)

    devices, current_connection = get_connection_info()
    try:
        connect_hotspot(wifi_obj, devices)
        client_unicast_communication(ctx, wifi_obj.client_ip, wifi_obj.server_ip, callback_loop)

    finally:
        switch_connections(wifi_obj.ssid, current_connection)
        ctx.term()


if __name__ == '__main__':
    from robonet.transmit_callbacks import transmit_cam_mjpg

    run(transmit_cam_mjpg)
