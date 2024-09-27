import zmq
import time
import socket
import camera
import cv2
import subprocess
import pathlib
import os
current_path = pathlib.Path(__file__).parent.resolve()

def get_local_ip():
    """Get the local IPv4 address of the server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def discovery_phase(radio, dish, local_ip):
    """Send pings to clients and discover their IP address."""
    client_ip = None
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
                return client_ip
        except zmq.Again:
            print("No client response yet")

class WifiSetupInfo:
    def __init__(self, ssid, server_ip, client_ip):
        self.ssid = ssid
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.password = "polylactic_acid"
        # todo: use ECDH to safely share a password
        #  https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ec/#cryptography.hazmat.primitives.asymmetric.ec.ECDH


import struct
def pack_obj(obj):
    class_name = obj.__class__.__name__
    obj_dict = obj.__dict__

    # Prepare the message
    message = struct.pack('!I', len(class_name))
    message += class_name.encode('utf-8')
    for key, value in obj_dict.items():
        key_encoded = key.encode('utf-8')
        value_encoded = str(value).encode('utf-8')
        message += struct.pack('!I', len(key_encoded))
        message += key_encoded
        message += struct.pack('!I', len(value_encoded))
        message += value_encoded

    # Send the entire message at once
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

def set_hotspot(wifi_obj:WifiSetupInfo, use_nmcli=True):
    devices = []
    if use_nmcli:
        try:
            result = subprocess.run("nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True, check=True, capture_output=True, text=True)
            devices = list(filter(None, result.stdout.split('\n')))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

        '''try:
            result = subprocess.run(f'nmcli device modify {devices[0]} ipv4.address {wifi_obj.server_ip} ipv4.method manual', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not set wifi ip: {e.stderr}")'''

        '''try:
            result = subprocess.run(f'nmcli radio wifi off ifname {devices[0]}', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not set wifi ip: {e.stderr}")'''
    else:
        try:
            result = subprocess.run(f'ifconfig {devices[0]} down', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not bring down wifi device: {e.stderr}")

        try:
            result = subprocess.run(f'ifconfig {devices[0]} {wifi_obj.server_ip}/24', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not set fixed ip for wifi device: {e.stderr}")

        try:
            result = subprocess.run(f'ifconfig {devices[0]} up', shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Could not bring up wifi device: {e.stderr}")

    try:
        command = f"nmcli device wifi hotspot ifname {devices[0]} ssid {wifi_obj.ssid} password {wifi_obj.password}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Setting wifi to access point failed: {e.stderr}")

    try:
        command = f"nmcli dev set {devices[0]} managed no"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could not stop managing the wifi device: {e.stderr}")

    try:
        command = f"iw dev | grep -Po '^\sInterface\s\K.*$'"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        result = None


    if result is not None and 'ah0' not in result.stdout:
        try:
            command = f"iw phy phy0 interface add ah0 type ibss"
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ncould not create iw ibss: {e.stderr}")

    try:
        command = f"ifconfig {devices[0]} down"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"could not join ibss: {e.stderr}")

    try:
        command = f"ifconfig ah0 up"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"could not join ibss: {e.stderr}")

    try:
        command = f"iw dev {devices[0]} ibss join {wifi_obj.ssid}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"could not join ibss: {e.stderr}")


def unset_hotspot(use_nmcli=True):
    try:
        command = f"iw dev ah0 ibss leave"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"could not join ibss: {e.stderr}")

    try:
        command = f"ifconfig ah0 down"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"could not join ibss: {e.stderr}")

    if use_nmcli:
        try:
            result = subprocess.run("nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True, check=True, capture_output=True, text=True)
            devices = list(filter(None, result.stdout.split('\n')))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

        try:
            command = f"ifconfig {devices[0]} up"
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"could not join ibss: {e.stderr}")

        try:
            command = f"nmcli dev set {devices[0]} managed yes"
            result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"nmcli could not start managing the wifi device: {e.stderr}")


def unicast_communication(ctx, local_ip, client_ip):
    """Start unicast communication between server and client."""
    unicast_radio = ctx.socket(zmq.RADIO)
    unicast_radio.setsockopt(zmq.SNDBUF, 2**15)
    unicast_radio.setsockopt(zmq.LINGER, 0)

    unicast_dish = ctx.socket(zmq.DISH)
    unicast_dish.setsockopt(zmq.RCVBUF, 2**15)
    unicast_dish.setsockopt(zmq.LINGER, 0)
    unicast_dish.rcvtimeo = 1000

    unicast_dish.bind(f"udp://{local_ip}:9998")
    unicast_dish.join("direct")
    unicast_radio.connect(f"udp://{client_ip}:9999")

    print(f"Starting unicast communication with client at {client_ip}...")

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

    unicast_dish.close()
    unicast_radio.close()

def run_server():
    """Main function to run the server."""
    ctx = zmq.Context.instance()

    # Discovery Phase
    radio = ctx.socket(zmq.RADIO)
    dish = ctx.socket(zmq.DISH)
    dish.rcvtimeo = 1000

    dish.bind("udp://239.0.0.1:9998")
    dish.join("discovery")
    radio.connect("udp://239.0.0.1:9999")

    local_ip = get_local_ip()
    client_ip = discovery_phase(radio, dish, local_ip)

    dish.close()
    radio.close()

    wifi_obj = WifiSetupInfo("robot_wifi", "192.168.2.1", "192.168.2.2")

    lazy_pirate_send_con_info(ctx, wifi_obj, local_ip, client_ip)

    hotspot_process = None
    try:
        hotspot_process = set_hotspot(wifi_obj)

        unicast_communication(ctx, wifi_obj.server_ip, wifi_obj.client_ip)
    finally:
        if hotspot_process is not None:
            hotspot_process.terminate()
            #hotspot_process.kill()
        unset_hotspot()
        ctx.term()


if __name__ == "__main__":
    run_server()
