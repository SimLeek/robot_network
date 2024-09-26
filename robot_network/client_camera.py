import zmq
import time
import socket
import camera

# Get the local IPv4 address of the client
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a remote server to get the local IP (this won't actually send data)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


# Initialize ZeroMQ context
ctx = zmq.Context()

# Create a Radio socket to send responses to the server
radio = ctx.socket(zmq.RADIO)
# Create a Dish socket to listen for pings from the server
dish = ctx.socket(zmq.DISH)
dish.rcvtimeo = 1000

# Bind the Dish socket to the UDP port and join the multicast group
dish.bind(f'udp://239.0.0.1:9999')  # Listen on multicast group
dish.join('discovery')  # Join the multicast group for discovery

# Connect the Radio socket for sending responses to the server
radio.connect(f'udp://239.0.0.1:9998')

# Get the local IPv4 address of the client
local_ip = get_local_ip()

# Main loop for discovery and communication
server_ip = None

while True:
    try:
        # Try to receive a ping from the server
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

# Close the multicast sockets
dish.close()
radio.close()

# Create new unicast sockets for direct communication
unicast_radio = ctx.socket(zmq.RADIO)
unicast_radio.setsockopt(zmq.LINGER, 0)
unicast_dish = ctx.socket(zmq.DISH)
unicast_dish.setsockopt(zmq.LINGER, 0)
unicast_dish.rcvtimeo = 1000

# Bind the client's unicast dish socket and connect the radio to the server's IP
unicast_dish.bind(f'udp://*:9999')
unicast_dish.join('direct')
unicast_radio.connect(f'udp://{server_ip}:9998')


print(f"Starting unicast communication with server at {server_ip}...")
cam = camera.CameraPack()
while True:
    try:
        # Receive direct messages from the server
        msg = unicast_dish.recv(copy=False)
        direct_message = msg.bytes.decode('utf-8')
        print(f"Received direct message: {direct_message}")
    except zmq.Again:
        print('No direct message yet')
        #time.sleep(1)

    # Send direct messages to the server
    direct_message = cam.get_packed_frame()
    parts = []
    for i in range(0,len(direct_message), 4096):
        parts.append(direct_message[i:i+4096])
    for p in parts[:-1]:
        unicast_radio.send(b'm'+p, group='direct')  # send more doesn't work either I guess
    unicast_radio.send(b'd'+parts[-1], group='direct')
    #direct_message = f"Direct message from client"
    #unicast_radio.send_multipart(parts, group='direct')
    print(f"Sent frame")
    time.sleep(1.0 / 120)  # limit 120 fps


unicast_dish.close()
unicast_radio.close()
ctx.term()
