from robonet import camera
import zmq
import time


def transmit_cam_mjpg(unicast_radio, unicast_dish):
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
            print(f"Sent frame")
            time.sleep(1.0 / 120)  # limit 120 fps
        except KeyboardInterrupt:
            break
