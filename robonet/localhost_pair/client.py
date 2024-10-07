import zmq

from robonet.util import get_local_ip, client_unicast_communication, client_udp_discovery


def run_client(callback):
    """Main function to run the client."""
    ctx = zmq.Context()

    client_unicast_communication(ctx, '127.0.0.1', '127.0.0.1', callback)

    ctx.term()


if __name__ == '__main__':
    from displayarray import display
    #from robonet.transmit_callbacks import transmit_cam_mjpg
    from robonet.receive_callbacks import receive_objs, display_fftnet
    obj_dict = dict()
    with display() as d:
        obj_dict['AudioBuffer'] = display_fftnet(d)
        run_client(receive_objs(obj_dict))
