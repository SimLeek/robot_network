import zmq
import time
import subprocess

from robonet.buffers.buffer_objects import WifiSetupInfo
from robonet.buffers.buffer_handling import pack_obj
from robonet.server_callbacks import display_mjpg_cv
from robonet.util import get_local_ip, switch_connections, get_connection_info


class AdHocServer(object):
    """Searches for a robot, transmits ad hoc connection info, then switches to the ad hoc connection as server"""

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
    def _lazy_pirate_send_con_info(ctx, obj, local_ip):
        """Lazy pirate server pattern sending direct connection info."""
        server = ctx.socket(zmq.REP)
        server.bind(f"tcp://{local_ip}:9998")

        _ = server.recv()
        response = pack_obj(obj)
        server.send(response)

        server.close()

    @staticmethod
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

    @staticmethod
    def _unicast_communication(ctx, local_ip, client_ip, callback_loop):
        """Start unicast communication between server and client."""
        unicast_radio = ctx.socket(zmq.RADIO)
        unicast_radio.setsockopt(zmq.LINGER, 0)
        unicast_dish = ctx.socket(zmq.DISH)
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
    def run(callback_loop):
        """Main function to run the server."""
        ctx = zmq.Context.instance()

        local_ip = get_local_ip()
        _ = AdHocServer._discovery_phase(ctx, local_ip)

        wifi_obj = WifiSetupInfo("robot_wifi", "192.168.2.1", "192.168.2.2")
        AdHocServer._lazy_pirate_send_con_info(ctx, wifi_obj, local_ip)

        devices, current_connection = get_connection_info()
        try:
            AdHocServer._set_hotspot(wifi_obj, devices)
            AdHocServer._unicast_communication(ctx, wifi_obj.server_ip, wifi_obj.client_ip, callback_loop)
        finally:
            switch_connections(wifi_obj.ssid, current_connection)
            ctx.term()


if __name__ == "__main__":
    AdHocServer.run(display_mjpg_cv)
