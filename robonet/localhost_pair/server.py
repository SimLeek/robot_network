"""Server communicating through UDP without disconnecting from the local Wi-Fi."""

import zmq
from robonet.util import get_local_ip, server_udp_discovery, server_unicast_communication


def run(callback):
    """Main function to run the server."""
    ctx = zmq.Context.instance()

    server_unicast_communication(ctx, '127.0.0.1', '127.0.0.1', callback)

    ctx.term()


if __name__ == "__main__":
    from robonet.transmit_callbacks import transmit_mic_fft_async
    run(transmit_mic_fft_async)
