import zmq
import time
import socket
import subprocess
import pathlib

from robot_network.buffers.buffer_objects import WifiSetupInfo
from robot_network.buffers.buffer_handling import pack_obj
from robot_network.server_callbacks import display_mjpg_cv

file_path = pathlib.Path(__file__).parent.resolve()



def get_local_ip():
    """Get the local IPv4 address of the server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class AdHocServer(object):

    @staticmethod
    def _discovery_phase(ctx, local_ip):
        """Send pings to clients and discover their IP address."""
        radio = ctx.socket(zmq.RADIO)
        dish = ctx.socket(zmq.DISH)
        dish.rcvtimeo = 1000

        dish.bind("udp://239.0.0.1:9998")
        dish.join("discovery")
        radio.connect("udp://239.0.0.1:9999")

        while True:
            message = f"PING from server: {local_ip}"
            radio.send(message.encode("utf-8"), group="discovery")
            print(f"Sent: {message}")
            time.sleep(1)

            try:
                msg = dish.recv(copy=False)
                client_message = msg.bytes.decode("utf-8")
                print(f"Received {msg.group}: {client_message}")

                if "PING_RESPONSE from client" in client_message:
                    client_ip = client_message.split(":")[-1].strip()
                    print(f"Discovered client IP: {client_ip}")
                    break
            except zmq.Again:
                print("No client response yet")

        dish.close()
        radio.close()

        return client_ip

    @staticmethod
    def _lazy_pirate_send_con_info(ctx, obj, local_ip, client_ip):
        """Lazy pirate server pattern sending direct connection info."""
        server = ctx.socket(zmq.REP)
        server.bind(f"tcp://{local_ip}:9998")

        print(f"Starting unicast communication with client at {client_ip}...")

        request = server.recv()
        response = pack_obj(obj)
        server.send(response)

        server.close()

    @staticmethod
    def _get_connection_info():
        """Get wifi devices for ad hoc and current network for resetting on end."""
        try:
            result = subprocess.run(
                "nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True,
                check=True, capture_output=True, text=True)
            devices = list(filter(None, result.stdout.split('\n')))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

        try:
            current_connection = subprocess.run(
                f"nmcli -t -f GENERAL.CONNECTION device show {devices[0]} | grep -oP 'GENERAL.CONNECTION:\K\w+'", shell=True,
                check=True, capture_output=True, text=True)
            devices = list(filter(None, result.stdout.split('\n')))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

        return devices, current_connection

    @staticmethod
    def _set_hotspot(wifi_obj: WifiSetupInfo, devices):
        """Set up an adhoc hotspot that matches the wifi_obj."""

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
                f"nmcli con modify {wifi_obj.ssid} 802-11-wireless.mode adhoc ipv4.addresses {wifi_obj.server_ip}/{prefix} ipv4.method manual ipv6.method ignore",
                f"nmcli con up {wifi_obj.ssid}",
            ])
            for command in commands:
                result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
                print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Setting wifi to access point failed: {e.stderr}")

    @staticmethod
    def _unset_hotspot(devices, wifi_obj, prior_connection):
        try:
            command = f"nmcli con down {wifi_obj.ssid}"
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"nmcli could shut down the adhoc connection: {e.stderr}")

        try:
            command = f"nmcli con up {prior_connection}"
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"nmcli could bring up the adhoc connection: {e.stderr}")

    @staticmethod

    def _unicast_communication(ctx, local_ip, client_ip, callback_loop):
        """Start unicast communication between server and client."""
        unicast_radio = ctx.socket(zmq.RADIO)
        unicast_radio.setsockopt(zmq.SNDBUF, 2 ** 15)
        unicast_radio.setsockopt(zmq.LINGER, 0)

        unicast_dish = ctx.socket(zmq.DISH)
        unicast_dish.setsockopt(zmq.RCVBUF, 2 ** 15)
        unicast_dish.setsockopt(zmq.LINGER, 0)
        unicast_dish.rcvtimeo = 1000

        unicast_dish.bind(f"udp://{local_ip}:9998")
        unicast_dish.join("direct")
        unicast_radio.connect(f"udp://{client_ip}:9999")

        print(f"Starting unicast communication with client at {client_ip}...")

        callback_loop(unicast_radio, unicast_dish)

        unicast_dish.close()
        unicast_radio.close()

    @staticmethod
    def run(callback_loop=display_mjpg_cv):
        """Main function to run the server."""
        ctx = zmq.Context.instance()

        local_ip = get_local_ip()
        client_ip = AdHocServer._discovery_phase(ctx, local_ip)

        wifi_obj = WifiSetupInfo("robot_wifi", "192.168.2.1", "192.168.2.2")

        AdHocServer._lazy_pirate_send_con_info(ctx, wifi_obj, local_ip, client_ip)

        devices, current_connection = AdHocServer._get_connection_info()

        try:
            AdHocServer._set_hotspot(wifi_obj, devices)
            AdHocServer._unicast_communication(ctx, wifi_obj.server_ip, wifi_obj.client_ip, callback_loop)
        finally:
            AdHocServer._unset_hotspot(devices, wifi_obj, current_connection)
            ctx.term()


if __name__ == "__main__":
    AdHocServer.run()
