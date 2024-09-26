import zmq
import time
import socket
import camera
import cv2


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

    # Unicast communication
    unicast_communication(ctx, local_ip, client_ip)

    ctx.term()


if __name__ == "__main__":
    run_server()
