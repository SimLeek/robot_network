# a.py: Ping script that discovers peers on the local network using IPv6 multicast

import zmq
import time

# Initialize ZeroMQ context
INTERFACE = "wlp0s20f0u2"

ctx = zmq.Context.instance()

# Create a Radio socket to send discovery messages
radio = ctx.socket(zmq.RADIO)
#radio.setsockopt( zmq.IPV6, True )
# Create a Dish socket to listen for client responses
dish = ctx.socket(zmq.DISH)
#dish.setsockopt( zmq.IPV6, True )
dish.rcvtimeo = 1000  # Timeout for receiving messages

# Bind the Dish socket to the UDP port and join a group
dish.bind(f'udp://*:9998')  # Listen on all interfaces
dish.join('discovery')  # Join the multicast group for discovery

# Connect the Radio socket to the same port (broadcast address)
radio.connect(f'udp://192.168.0.*:9999')  # Multicast address for IPv6

# Function to send a ping to all clients
def send_ping():
    message = f"PING from server"
    radio.send(message.encode('utf-8'), group='discovery')
    print(f"Sent: {message}")

# Main loop for discovery and communication
while True:
    send_ping()
    time.sleep(1)

    try:
        # Try to receive a message from any client
        msg = dish.recv(copy=False)
        client_message = msg.bytes.decode('utf-8')
        print(f"Received {msg.group}: {client_message}")

        # After discovering a client, break and initiate direct communication
        break
    except zmq.Again:
        print('No client response yet')

# Direct communication after discovery
print(f"Client discovered, starting direct communication...")
dish.join('direct')

while True:
    # Example of sending direct communication to the client
    direct_message = f"Direct message from server"
    radio.send(direct_message.encode('utf-8'), group='direct')
    print(f"Sent: {direct_message}")
    time.sleep(1)

dish.close()
radio.close()
ctx.term()
