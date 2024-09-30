import zmq

from robonet.util import get_local_ip, client_unicast_communication, client_udp_discovery


def run_client(callback):
    """Main function to run the client."""
    ctx = zmq.Context()

    local_ip = get_local_ip()
    server_ip = client_udp_discovery(ctx, local_ip)

    client_unicast_communication(ctx, local_ip, server_ip, callback)

    ctx.term()


if __name__ == '__main__':
    from robonet.client_callbacks import transmit_cam_mjpg

    run_client(transmit_cam_mjpg)
