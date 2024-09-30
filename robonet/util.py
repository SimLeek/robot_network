import socket
import subprocess
import time
import zmq

def get_local_ip():
    """Get the local IPv4 address of the server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def get_connection_info():
    """Get Wi-Fi devices for ad hoc and current network for resetting on end."""
    try:
        result = subprocess.run(
            "nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

    try:
        current_connection = subprocess.run(
            f"nmcli -t -f GENERAL.CONNECTION device show {devices[0]} | grep -oP 'GENERAL.CONNECTION:\\K\\w+'",
            shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

    return devices, current_connection


def switch_connections(current_connection, next_connection):
    try:
        command = f"nmcli con down {current_connection}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could shut down the adhoc connection: {e.stderr}")

    try:
        command = f"nmcli con up {next_connection}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could bring up the adhoc connection: {e.stderr}")

def server_udp_discovery(ctx, local_ip):
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

def client_udp_discovery(ctx, local_ip):
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

def server_unicast_communication(ctx, local_ip, client_ip, callback_loop):
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

def client_unicast_communication(ctx, local_ip, server_ip, callback_loop):
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