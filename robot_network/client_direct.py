import zmq
import time
import socket
import camera
import subprocess

def get_local_ip():
    """Get the local IPv4 address of the client."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()

def discovery_phase(radio, dish, local_ip):
    """Listen for pings from the server and respond with the client's IP."""
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
    return server_ip

import struct
def unpack_obj(message):
    # Unpack the message
    offset = 0
    class_name_len = struct.unpack_from('!I', message, offset)[0]
    offset += 4
    class_name = message[offset:offset + class_name_len].decode('utf-8')
    offset += class_name_len

    obj_dict = {}
    while offset < len(message):
        key_len = struct.unpack_from('!I', message, offset)[0]
        offset += 4
        key = message[offset:offset + key_len].decode('utf-8')
        offset += key_len
        value_len = struct.unpack_from('!I', message, offset)[0]
        offset += 4
        value = message[offset:offset + value_len].decode('utf-8')
        offset += value_len
        obj_dict[key] = value

    received_obj = type(class_name, (), {})()
    for key, value in obj_dict.items():
        setattr(received_obj, key, value)

    return received_obj

def lazy_pirate_recv_con_info(ctx, local_ip, server_ip, timeout=2500, retries=10):
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

def connect_hotspot(wifi_obj, use_nmcli=True):
    if use_nmcli:
        try:
            result = subprocess.run(
                "nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True,
                check=True, capture_output=True, text=True)
            devices = list(filter(None, result.stdout.split('\n')))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

        '''try:
            result = subprocess.run(f'nmcli device modify {devices[0]} ipv4.address {wifi_obj.client_ip} ipv4.method manual', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not set wifi ip: {e.stderr}")'''

        '''try:
            while True:
                result = subprocess.run(f"nmcli -t -f SSID device wifi list ifname {devices[0]}", shell=True, check=True, capture_output=True, text=True)
                print(f"Scan results:\n{result.stdout}")
                if wifi_obj.ssid in result.stdout:
                    break
                time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not list wifi access points: {e.stderr}")'''

        try:
            command = f"nmcli connection show | grep {wifi_obj.ssid}"
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
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

    else:
        raise NotImplementedError('nmcli only for now')

def unset_hotspot(use_nmcli=True):
    if use_nmcli:
        try:
            result = subprocess.run("nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True, check=True, capture_output=True, text=True)
            devices = list(filter(None, result.stdout.split('\n')))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

        # go back to dhcp
        try:
            result = subprocess.run(f'nmcli device modify {devices[0]} ipv4.address "" ipv4.method auto', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not set wifi ip: {e.stderr}")


def unicast_communication(ctx, local_ip, server_ip):
    """Start unicast communication between client and server."""
    unicast_radio = ctx.socket(zmq.RADIO)
    unicast_dish = ctx.socket(zmq.DISH)
    unicast_dish.rcvtimeo = 1000

    unicast_dish.bind(f'udp://{local_ip}:9999')
    unicast_dish.join('direct')
    unicast_radio.connect(f'udp://{server_ip}:9998')

    print(f"Starting unicast communication with server at {server_ip}...")
    cam = camera.CameraPack()
    while True:
        try:
            try:
                # Receive direct messages from the server
                msg = unicast_dish.recv(copy=False)
                direct_message = msg.bytes.decode('utf-8')
                print(f"Received direct message: {direct_message}")
            except zmq.Again:
                print('No direct message yet')
                # time.sleep(1)

            # Send direct messages to the server
            direct_message = cam.get_packed_frame()
            parts = []
            for i in range(0, len(direct_message), 4096):
                parts.append(direct_message[i:i + 4096])
            for p in parts[:-1]:
                unicast_radio.send(b'm' + p, group='direct')  # send more doesn't work either I guess
            unicast_radio.send(b'd' + parts[-1], group='direct')
            # direct_message = f"Direct message from client"
            # unicast_radio.send_multipart(parts, group='direct')
            print(f"Sent frame")
            time.sleep(1.0 / 120)  # limit 120 fps
        except KeyboardInterrupt:
            break

    unicast_dish.close()
    unicast_radio.close()

def run_client():
    """Main function to run the client."""
    ctx = zmq.Context()

    radio = ctx.socket(zmq.RADIO)
    dish = ctx.socket(zmq.DISH)
    dish.rcvtimeo = 1000

    dish.bind('udp://239.0.0.1:9999')
    dish.join('discovery')
    radio.connect('udp://239.0.0.1:9998')

    local_ip = get_local_ip()

    # Discovery Phase
    server_ip = discovery_phase(radio, dish, local_ip)

    dish.close()
    radio.close()

    wifi_obj = lazy_pirate_recv_con_info(ctx, local_ip, server_ip)

    try:
        connect_hotspot(wifi_obj)

        unicast_communication(ctx, wifi_obj.client_ip, wifi_obj.server_ip)

    finally:
        unset_hotspot()
        ctx.term()

if __name__ == '__main__':
    run_client()
