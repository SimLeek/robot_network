import time
import struct
import zmq


class WifiSetupInfo:
    def __init__(self, ssid, server_ip, client_ip):
        self.ssid = ssid
        self.server_ip = server_ip
        self.client_ip = client_ip


class TinyHandshakeStateMachine:
    """Handshake class for sending small objects without crc"""

    def __init__(self, radio, dish, timeout=10):
        self.radio = radio
        self.dish = dish
        self.obj = None
        self.state = "INIT"
        self.handshake_complete = False
        self.timeout = timeout

    def send_obj(self):
        class_name = self.obj.__class__.__name__
        obj_dict = self.obj.__dict__

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
        self.radio.send(message)
        print(f"Sent object: {self.obj.__class__.__name__}")
        self.state = "WAIT_FOR_ACK"

    def wait_for_obj(self):
        start_time = time.time()
        while True:
            try:
                message = self.dish.recv(flags=zmq.NOBLOCK)
                try:
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

                    if offset != len(message):  # offsets weren't correct or we receveid more data than we should've
                        self.state = "SEND_NACK"
                    else:
                        # Create object
                        received_obj = type(class_name, (), {})()
                        for key, value in obj_dict.items():
                            setattr(received_obj, key, value)

                        print(f"Received object: {class_name}")

                        if self.verify_obj(received_obj):
                            self.obj = received_obj
                            self.state = "SEND_ACK"
                        else:
                            self.state = "SEND_NACK"
                except (struct.error, IndexError):
                    print("Error unpacking message, sending NACK")
                    self.state = "SEND_NACK"
                return
            except zmq.Again:
                if time.time() - start_time > self.timeout and isinstance(self, ServerTinyHandshakeStateMachine):
                    print("Timeout waiting for object, going back to SEND_OBJ")
                    self.state = "SEND_OBJ"
                    return
                time.sleep(0.01)

    def verify_obj(self, received_obj):
        # Base implementation always returns True
        return True

    def send_ack(self):
        self.radio.send("ACK".encode('utf-8'))
        print("Sent ACK")
        self.state = "HANDSHAKE_COMPLETE"

    def send_nack(self):
        self.radio.send("NACK".encode('utf-8'))
        print("Sent NACK")
        self.state = "WAIT_FOR_OBJ"

    def wait_for_ack(self):
        start_time = time.time()
        while True:
            try:
                msg = self.dish.recv(flags=zmq.NOBLOCK)
                response = msg.decode('utf-8')
                if response == "ACK":
                    print("Received ACK")
                    self.state = "HANDSHAKE_COMPLETE"
                elif response == "NACK":
                    print("Received NACK")
                    self.state = "SEND_OBJ"
                return
            except zmq.Again:
                if time.time() - start_time > self.timeout:
                    print("Timeout waiting for ACK, going back to SEND_OBJ")
                    self.state = "SEND_OBJ"
                    return
                time.sleep(0.01)


class ServerTinyHandshakeStateMachine(TinyHandshakeStateMachine):
    def __init__(self, radio, dish, obj, timeout=10):
        super().__init__(radio, dish, timeout)
        self.obj = obj

    def verify_obj(self, received_obj):
        if self.obj.__class__.__name__ != received_obj.__class__.__name__:
            return False
        for key, value in self.obj.__dict__.items():
            if key not in received_obj.__dict__ or received_obj.__dict__[key] != value:
                return False
        return True

    def transition(self):
        if self.state == "INIT":
            self.state = "SEND_OBJ"
        elif self.state == "SEND_OBJ":
            self.send_obj()
        elif self.state == "WAIT_FOR_ACK":
            self.wait_for_ack()
        elif self.state == "WAIT_FOR_OBJ":
            self.wait_for_obj()
        elif self.state == "SEND_ACK":
            self.send_ack()
        elif self.state == "SEND_NACK":
            self.send_nack()
        elif self.state == "HANDSHAKE_COMPLETE":
            self.handshake_complete = True


class ClientTinyHandshakeStateMachine(TinyHandshakeStateMachine):
    def transition(self):
        if self.state == "INIT":
            self.state = "WAIT_FOR_OBJ"
        elif self.state == "WAIT_FOR_OBJ":
            self.wait_for_obj()
        elif self.state == "SEND_ACK":
            self.send_ack()
        elif self.state == "SEND_NACK":
            self.send_nack()
        elif self.state == "SEND_OBJ":
            self.send_obj()
        elif self.state == "WAIT_FOR_ACK":
            self.wait_for_ack()
        elif self.state == "HANDSHAKE_COMPLETE":
            self.handshake_complete = True


def run_handshake(state_machine):
    while not state_machine.handshake_complete:
        state_machine.transition()
        time.sleep(0.01)
    return state_machine.obj


# Example usage
if __name__ == '__main__':
    ctx = zmq.Context.instance()

    # For server
    server_radio = ctx.socket(zmq.RADIO)
    server_dish = ctx.socket(zmq.DISH)
    server_dish.bind('udp://192.168.1.1:9998')
    server_radio.connect('udp://192.168.1.2:9999')

    wifi_setup_info = WifiSetupInfo("test_network", "192.168.1.1", "192.168.1.2")
    server = ServerTinyHandshakeStateMachine(server_radio, server_dish, wifi_setup_info)
    server_result = run_handshake(server)
    print(f"Server handshake complete. Result: {server_result.__dict__}")

    # For client
    client_radio = ctx.socket(zmq.RADIO)
    client_dish = ctx.socket(zmq.DISH)
    client_dish.bind('udp://192.168.1.2:9999')
    client_radio.connect('udp://192.168.1.1:9998')

    client = ClientTinyHandshakeStateMachine(client_radio, client_dish)
    client_result = run_handshake(client)
    print(f"Client handshake complete. Result: {client_result.__dict__}")
