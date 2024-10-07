from robonet import camera
import zmq
import time
from robonet.buffers.buffer_objects import MJpegCamFrame, AudioBuffer
from robonet.buffers.buffer_handling import pack_obj
import sounddevice as sd
from scipy import fft
import numpy as np

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

def transmit_cam_mjpg_async(unicast_radio):
    cam = camera.CameraPack()
    while True:
        # Send direct messages to the server
        direct_message = cam.get_packed_frame()
        direct_message = MJpegCamFrame(0, 0, direct_message)
        direct_message = pack_obj(direct_message)
        parts = []
        for i in range(0, len(direct_message), 4096):
            parts.append(direct_message[i:i + 4096])
        for p in parts[:-1]:
            unicast_radio.send(b'm' + p, group='direct')  # send more doesn't work either I guess
        unicast_radio.send(b'd' + parts[-1], group='direct')
        print(f"Sent frame")
        time.sleep(1.0 / 120)  # limit 120 fps


def transmit_mic_fft_async(unicast_radio, unicast_dish, sample_rate=44800, sends_per_sec=24, fft_size=1536, channels=1):
    block_size = sample_rate//sends_per_sec
    def audio_callback(indata, outdata, frames, time, status):
        nonlocal fft_size, unicast_radio

        x = fft.rfft(indata, n=block_size, axis=0)

        fft_size = min(len(indata) * 2, fft_size)
        fft_transmit = x[:fft_size // 2]

        direct_message = AudioBuffer(sample_rate, sends_per_sec, fft_transmit)
        direct_message = pack_obj(direct_message)
        parts = []
        for i in range(0, len(direct_message), 4096):
            parts.append(direct_message[i:i + 4096])
        for p in parts[:-1]:
            unicast_radio.send(b'm' + p, group='direct')  # send more doesn't work either I guess
        unicast_radio.send(b'd' + parts[-1], group='direct')
        print(f"Sent fft")

    with sd.Stream(channels=channels, samplerate=sample_rate, blocksize=block_size, callback=audio_callback):
        print("Audio streaming started.")
        while True:
            time.sleep(0) # leave thread while mic works


