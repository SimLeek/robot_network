import zmq
import time
import socket
import camera


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

    # Unicast communication
    unicast_communication(ctx, local_ip, server_ip)

    ctx.term()


if __name__ == '__main__':
    run_client()
