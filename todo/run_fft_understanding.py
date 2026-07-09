import asyncio

import zmq

from robonet.util import get_local_ip, client_unicast_communication, client_udp_discovery
from robonet.buffers.buffer_objects import AudioBuffer
import numpy as np
import cv2
from scipy.interpolate import interp1d
from scipy.ndimage import convolve1d

def create_pyramid(x, min_size=1):
    pyramid = [x]
    current = x
    while current.shape[0] > min_size:
        current = cv2.resize(current, (current.shape[1], current.shape[0]//2), interpolation=cv2.INTER_LINEAR)
        pyramid.append(current)
    return pyramid

def apply_edge_detection(signal, axis=0):
    """Applies a simple edge detection filter on a 1D signal."""
    edge_filter = np.array([-.5, 1, -.5])  # Basic finite difference kernel
    return convolve1d(signal, edge_filter, axis=axis, mode='constant')


def attractor_network(signal, radius=3):
    """
    Simple attractor network implementation for 1D signals.
    Attracts nearby values to +1 or -1 and inhibits distal values to 0.

    Args:
        signal: 1D input array to process.
        iterations: Number of iterations for the network to converge.
        radius: Neighborhood radius for attraction.

    Returns:
        Processed signal.
    """
    updated_signal = signal.copy()
    for i in range(len(signal)):
        # Define the neighborhood
        start = max(0, i - radius)
        end = min(len(signal), i + radius+1)

        # Compute local attraction
        neighborhood = updated_signal[start:end]
        neighborhood[neighborhood<0] = 0
        neighbor_avg = np.linalg.norm(neighborhood)
        neighborhood = neighborhood/neighbor_avg
        weighted_center = np.sum(np.abs(neighborhood[:,0])*np.linspace(start, end, num=end-start, endpoint=False))

        # Attraction rule
        if abs(i-weighted_center) > radius/2+1:
            updated_signal[i,0] -= neighbor_avg/radius
        elif abs(i-weighted_center) < radius/2+1:
            updated_signal[i,0] += neighbor_avg/radius

    updated_signal2 = signal.copy()
    for i in range(len(signal)):
        # Define the neighborhood
        start = max(0, i - radius)
        end = min(len(signal), i + radius + 1)

        # Compute local attraction
        neighborhood = updated_signal2[start:end]
        neighborhood[neighborhood>0] = 0
        neighbor_avg = np.linalg.norm(neighborhood)
        neighborhood = neighborhood/neighbor_avg
        weighted_center = np.sum(np.abs(neighborhood[:,0])*np.linspace(start, end, num=end-start, endpoint=False))

        # Attraction rule
        if abs(i-weighted_center) > radius/2+1:
            updated_signal2[i,0] += neighbor_avg/radius
        elif abs(i-weighted_center) < radius/2+1:
            updated_signal2[i,0] -= neighbor_avg/radius

    signal = np.clip(updated_signal, -1, 1)  # Update the signal for the next iteration
    print(signal)

    return signal

def display_fftnet(displayer):
    fft_mag_double_attr = fft_mag_quadruple_attr = None
    def fft_to_nnet(obj:AudioBuffer):
        nonlocal fft_mag_double_attr, fft_mag_quadruple_attr
        fft_size = obj.fft_data.shape[0]*2
        fft_mag = np.abs(obj.fft_data) / (fft_size // 2)

        # magnify lower amplitudes
        fft_mag = np.sqrt(fft_mag)  # sounddevice sets mag to -1 to 1, so sqrt is fine

        fft_phase = np.angle(obj.fft_data)  # -pi to pi phase
        max_pha = np.max(fft_phase)
        min_pha = np.min(fft_phase)
        fft_phase = (fft_phase - min_pha) / (max_pha - min_pha)
        fft_phase[np.isnan(fft_phase)] = 0
        fft_phase = fft_phase*fft_mag

        fft_list = []
        for i in range(fft_mag.shape[1]):
            fft_list.append(fft_mag[:, i])
            fft_list.append(fft_phase[:, i])

        full_fft = np.stack(fft_list, axis=-1)

        fft_pyr = create_pyramid(full_fft)

        #for e, fft_p in enumerate(fft_pyr):
        #    displayer.displayer.imshow(f'fft {e}', fft_p)
        #    edge_detected = apply_edge_detection(fft_p[:, 0], axis=0)  # Edge on mag part
        #    displayer.displayer.imshow(f'efft {e} (edges)', (edge_detected[..., np.newaxis]+1)/2)


        freq_bins = np.fft.rfftfreq(fft_size, d=1 / obj.sample_rate)
        index_4000hz = int(4000 / freq_bins[1])
        fft_mag_0_4000 = fft_mag[:index_4000hz, :]  # Extract up to 1000 Hz with extra bins

        # Interpolate with cubic interpolation
        x_original = np.arange(fft_mag_0_4000.shape[0])
        x_double = np.linspace(1, x_original[-1], num=fft_mag_0_4000.shape[0] * 2)
        x_quadruple = np.linspace(1, x_original[-1], num=fft_mag_0_4000.shape[0] * 4)

        interp_func = interp1d(x_original, fft_mag_0_4000, axis=0, kind='cubic')
        fft_mag_double = interp_func(x_double)
        fft_mag_quadruple = interp_func(x_quadruple)

        # Display the interpolated results
        displayer.displayer.imshow('1fft_mag_double (interpolated)', fft_mag_double)
        displayer.displayer.imshow('2fft_mag_quadruple (interpolated)', fft_mag_quadruple)

        # Apply edge detection on interpolated results
        fft_mag_double_edges = apply_edge_detection(fft_mag_double, axis=0)
        fft_mag_quadruple_edges = apply_edge_detection(fft_mag_quadruple, axis=0)

        displayer.displayer.imshow('3fft_mag_double (edges)', (fft_mag_double_edges+1)/2)
        displayer.displayer.imshow('4fft_mag_quadruple (edges)', (fft_mag_quadruple_edges+1)/2)


        displayer.displayer.update()

    return fft_to_nnet

async def run_client(callback):
    """Main function to run the client."""
    ctx = zmq.Context()

    await client_unicast_communication(ctx, '127.0.0.1', '127.0.0.1', callback)

    ctx.term()


if __name__ == '__main__':
    from displayarray import display
    from todo.plain_receive_callbacks import receive_objs
    obj_dict = dict()
    with display() as d:
        obj_dict['AudioBuffer'] = display_fftnet(d)
        asyncio.run(run_client(receive_objs(obj_dict)))
