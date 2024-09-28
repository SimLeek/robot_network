import zmq
import time
import socket
import camera
import cv2
import subprocess
import pathlib
import struct

file_path = pathlib.Path(__file__).parent.resolve()


def get_local_ip():
    """Get the local IPv4 address of the server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def discovery_phase(ctx, local_ip):
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


class WifiSetupInfo:
    """Wifi info buffer object. Needs to be the same on both sides."""
    def __init__(self, ssid, server_ip, client_ip):
        self.ssid = ssid
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.password = "polylactic_acid"
        # todo: use ECDH to safely share a password
        #  https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ec/#cryptography.hazmat.primitives.asymmetric.ec.ECDH


def pack_obj(obj):
    """Pack any object into a byte string for sending over a network."""
    class_name = obj.__class__.__name__
    obj_dict = obj.__dict__

    message = struct.pack('!I', len(class_name))
    message += class_name.encode('utf-8')
    for key, value in obj_dict.items():
        key_encoded = key.encode('utf-8')
        value_encoded = str(value).encode('utf-8')
        message += struct.pack('!I', len(key_encoded))
        message += key_encoded
        message += struct.pack('!I', len(value_encoded))
        message += value_encoded

    return message


def lazy_pirate_send_con_info(ctx, obj, local_ip, client_ip):
    """Lazy pirate server pattern sending direct connection info."""
    server = ctx.socket(zmq.REP)
    server.bind(f"tcp://{local_ip}:9998")

    print(f"Starting unicast communication with client at {client_ip}...")

    request = server.recv()
    response = pack_obj(obj)
    server.send(response)

    server.close()


def get_connection_info():
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

def set_hotspot(wifi_obj: WifiSetupInfo, devices):
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


def unset_hotspot(devices, wifi_obj, prior_connection):
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


def cv_communication(unicast_radio, unicast_dish):
    while True:
        try:
            direct_message = f"Direct message from server"
            unicast_radio.send(direct_message.encode("utf-8"), group="direct")
            print(f"Sent: {direct_message}")

            try:
                msg = unicast_dish.recv(copy=False)
                msg_bytes = [msg.bytes[1:]]
                while msg.bytes[0] == ord(b"m"):  # snd more doesn't work for udp
                    msg = unicast_dish.recv(copy=False)
                    msg_bytes.append(msg.bytes[1:])
                msg = b"".join(msg_bytes)

                width, height, jpg_bytes, test_variable = (
                    camera.CameraPack.unpack_frame(msg)
                )
                img = camera.CameraPack.to_cv2_image(jpg_bytes)
                try:
                    if img is not None and img.size > 0:
                        cv2.imshow("Camera Stream", img)
                except cv2.error as e:
                    print(f"OpenCV error: {e}")
                if cv2.waitKey(1) == 27:  # Press 'ESC' to exit
                    break

                print(f"Received direct message: {test_variable}")
            except zmq.Again:
                print("No direct message yet")
                time.sleep(1.0 / 120)
        except KeyboardInterrupt:
            break

def unicast_communication(ctx, local_ip, client_ip, callback_loop):
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


def run_server(callback_loop=cv_communication):
    """Main function to run the server."""
    ctx = zmq.Context.instance()

    local_ip = get_local_ip()
    client_ip = discovery_phase(ctx, local_ip)

    wifi_obj = WifiSetupInfo("robot_wifi", "192.168.2.1", "192.168.2.2")

    lazy_pirate_send_con_info(ctx, wifi_obj, local_ip, client_ip)

    devices, current_connection = get_connection_info()

    try:
        set_hotspot(wifi_obj, devices)
        unicast_communication(ctx, wifi_obj.server_ip, wifi_obj.client_ip, callback_loop)
    finally:
        unset_hotspot(devices, wifi_obj, current_connection)
        ctx.term()


if __name__ == "__main__":
    run_server()
