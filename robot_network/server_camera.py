import zmq
import time
import socket
import camera
import cv2

# Get the local IPv4 address of the server
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a remote server to get the local IP (this won't actually send data)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


# Initialize ZeroMQ context
ctx = zmq.Context.instance()

# Create a Radio socket to send discovery messages
radio = ctx.socket(zmq.RADIO)
# Create a Dish socket to listen for client responses
dish = ctx.socket(zmq.DISH)
dish.rcvtimeo = 1000  # Timeout for receiving messages

# Bind the Dish socket to the UDP port and join a multicast group
dish.bind(f'udp://239.0.0.1:9998')  # Listen on multicast group
dish.join('discovery')  # Join the multicast group for discovery

# Connect the Radio socket to the same multicast group
radio.connect(f'udp://239.0.0.1:9999')

# Get the local IPv4 address of the server
local_ip = get_local_ip()


# Function to send a ping to all clients along with the server's IP address
def send_ping():
    message = f"PING from server: {local_ip}"
    radio.send(message.encode('utf-8'), group='discovery')
    print(f"Sent: {message}")


# Main loop for discovery and communication
client_ip = None
while True:
    send_ping()
    time.sleep(1)

    try:
        # Try to receive a message from any client
        msg = dish.recv(copy=False)
        client_message = msg.bytes.decode('utf-8')
        print(f"Received {msg.group}: {client_message}")

        # Parse the client's IP address from the response
        if "PING_RESPONSE from client" in client_message:
            client_ip = client_message.split(":")[-1].strip()
            print(f"Discovered client IP: {client_ip}")
            break
    except zmq.Again:
        print('No client response yet')

# Close the multicast sockets
dish.close()
radio.close()

# Create new unicast sockets for direct communication
unicast_radio = ctx.socket(zmq.RADIO)
unicast_radio.setsockopt(zmq.SNDBUF, 2**15)
unicast_radio.setsockopt(zmq.LINGER, 0)
unicast_dish = ctx.socket(zmq.DISH)
unicast_dish.setsockopt(zmq.RCVBUF, 2**15)
unicast_dish.setsockopt(zmq.LINGER, 0)
unicast_dish.rcvtimeo = 1000

# Bind the server's unicast dish socket and connect the radio to the client's IP
unicast_dish.bind(f'udp://{local_ip}:9998')
unicast_dish.join('direct')
unicast_radio.connect(f'udp://{client_ip}:9999')


print(f"Starting unicast communication with client at {client_ip}...")

while True:
    try:
        # Send direct messages to the client
        direct_message = f"Direct message from server"
        unicast_radio.send(direct_message.encode('utf-8'), group='direct')
        print(f"Sent: {direct_message}")

        try:
            # Receive direct messages from the client
            msg = unicast_dish.recv(copy=False)
            msg_bytes = []
            msg_bytes.append(msg.bytes[1:])
            while msg.bytes[0]==ord(b'm'):  # snd more doesn't work for udp
                msg = unicast_dish.recv(copy=False)
                msg_bytes.append(msg.bytes[1:])
            msg = b''.join(msg_bytes)
            width, height, jpg_bytes, test_variable = camera.CameraPack.unpack_frame(msg)
            img = camera.CameraPack.to_cv2_image(jpg_bytes)
            try:
                if img and img.size>0:
                    cv2.imshow('Camera Stream', img)
            except cv2.error as e:
                print(f"OpenCV error: {e}")
            if cv2.waitKey(1) == 27:  # Press 'ESC' to exit
                break

            #direct_message = msg.bytes.decode('utf-8')
            print(f"Received direct message: {test_variable}")
        except zmq.Again:
            print('No direct message yet')
            time.sleep(1.0/120)
    except KeyboardInterrupt:
        break


unicast_dish.close()
unicast_radio.close()
ctx.term()
