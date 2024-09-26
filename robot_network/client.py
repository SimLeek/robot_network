# b.py: Response script that listens and responds to pings from peers using IPv6 multicast

import zmq
import time

# Constants
INTERFACE = "wlp0s20f0u2"
MULTICAST_GROUP = f'192.168.0.*'  # Link-local all-nodes multicast address
PORT = 9999

# Initialize ZeroMQ context and socket for receiving pings (multicast)
context = zmq.Context()
radio = context.socket(zmq.RADIO)
#radio.setsockopt( zmq.IPV6, True )

dish = context.socket(zmq.DISH)
#dish.setsockopt( zmq.IPV6, True )
dish.rcvtimeo = 1000

# Bind the Dish socket to the UDP port and join the multicast group
dish.bind(f'udp://239.0.0.1:9999')  # Listen on all interfaces
dish.join('discovery')  # Join the multicast group for discovery

# Connect the Radio socket for sending responses to the server
radio.connect(f'udp://239.0.0.1:9998')  # Multicast address for IPv6

# Main loop for discovery and communication
while True:
    try:
        # Try to receive a ping from the server
        msg = dish.recv(copy=False)
        server_message = msg.bytes.decode('utf-8')
        print(f"Received {msg.group}: {server_message}")

        # Respond to the server after receiving a ping
        response_message = f"PING_RESPONSE from client"
        radio.send(response_message.encode('utf-8'), group='discovery')
        print(f"Responded: {response_message}")

        # After responding, break and prepare for direct communication
        break
    except zmq.Again:
        print('No ping received from server')

# Direct communication after discovery
print(f"Server discovered, starting direct communication...")
dish.join('direct')

while True:
    try:
        # Listen for direct messages from the server
        msg = dish.recv(copy=False)
        direct_message = msg.bytes.decode('utf-8')
        print(f"Received direct message: {direct_message}")
    except zmq.Again:
        print('No direct message yet')
        time.sleep(1)

dish.close()
radio.close()
ctx.term()
