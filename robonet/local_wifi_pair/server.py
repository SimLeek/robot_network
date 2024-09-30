"""Server communicating through UDP without disconnecting from the local Wi-Fi."""

import zmq
from robonet.util import get_local_ip, server_udp_discovery, server_unicast_communication


def run(callback):
    """Main function to run the server."""
    ctx = zmq.Context.instance()

    local_ip = get_local_ip()
    client_ip = server_udp_discovery(ctx, local_ip)

    server_unicast_communication(ctx, local_ip, client_ip, callback)

    ctx.term()


if __name__ == "__main__":
    from robonet.server_callbacks import display_mjpg_cv

    run(display_mjpg_cv)
