import time

import cv2
import zmq

from robot_network import camera


def display_mjpg_cv(unicast_radio, unicast_dish):
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
