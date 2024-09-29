import zmq
import subprocess

from robonet.buffers.buffer_handling import unpack_obj
from robonet.client_callbacks import transmit_cam_mjpg
from robonet.util import get_local_ip, switch_connections, get_connection_info


class AdHocClient(object):
    """Searches for a server, receives ad hoc connection info, then switches to the ad hoc connection as robot"""

    @staticmethod
    def _discovery_phase(ctx, local_ip):
        """Listen for pings from the server and respond with the client's IP."""
        radio = ctx.socket(zmq.RADIO)
        dish = ctx.socket(zmq.DISH)
        dish.rcvtimeo = 1000

        dish.bind('udp://239.0.0.1:9999')
        dish.join('discovery')
        radio.connect('udp://239.0.0.1:9998')

        server_ip = None
        while True:
            try:
                msg = dish.recv(copy=False)
                server_message = msg.bytes.decode('utf-8')
                print(f"Received {msg.group}: {server_message}")

                # Parse the server's IP address from the ping message
                if "PING from server" in server_message:
                    server_ip = server_message.split(":")[-1].strip()
                    print(f"Discovered server IP: {server_ip}")

                # Respond to the server with the client's IP address
                response_message = f"PING_RESPONSE from client: {local_ip}"
                radio.send(response_message.encode('utf-8'), group='discovery')
                print(f"Responded: {response_message}")

                # After responding, break and prepare for direct communication
                break
            except zmq.Again:
                print('No ping received from server')

        dish.close()
        radio.close()

        return server_ip

    @staticmethod
    def _lazy_pirate_recv_con_info(ctx, server_ip, timeout=2500, retries=10):
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

    @staticmethod
    def _connect_hotspot(wifi_obj, devices):
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

    @staticmethod
    def _unicast_communication(ctx, local_ip, server_ip, callback_loop):
        """Start unicast communication between client and server."""
        unicast_radio = ctx.socket(zmq.RADIO)
        unicast_radio.setsockopt(zmq.LINGER, 0)
        unicast_dish = ctx.socket(zmq.DISH)
        unicast_dish.setsockopt(zmq.LINGER, 0)
        unicast_dish.rcvtimeo = 1000

        unicast_dish.bind(f'udp://{local_ip}:9999')
        unicast_dish.join('direct')
        unicast_radio.connect(f'udp://{server_ip}:9998')

        print(f"Starting unicast communication with server at {server_ip}...")
        callback_loop(unicast_radio, unicast_dish)

        unicast_dish.close()
        unicast_radio.close()

    @staticmethod
    def run(callback_loop):
        """Main function to run the client."""
        ctx = zmq.Context.instance()

        local_ip = get_local_ip()
        server_ip = AdHocClient._discovery_phase(ctx, local_ip)

        wifi_obj = AdHocClient._lazy_pirate_recv_con_info(ctx, server_ip)

        devices, current_connection = get_connection_info()
        try:
            AdHocClient._connect_hotspot(wifi_obj, devices)
            AdHocClient._unicast_communication(ctx, wifi_obj.client_ip, wifi_obj.server_ip, callback_loop)

        finally:
            switch_connections(wifi_obj.ssid, current_connection)
            ctx.term()


if __name__ == '__main__':
    AdHocClient.run(transmit_cam_mjpg)
