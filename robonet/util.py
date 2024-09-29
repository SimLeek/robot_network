import socket
import subprocess


def get_local_ip():
    """Get the local IPv4 address of the server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def get_connection_info():
    """Get Wi-Fi devices for ad hoc and current network for resetting on end."""
    try:
        result = subprocess.run(
            "nmcli --get-values GENERAL.DEVICE,GENERAL.TYPE device show | sed '/^wifi/!{h;d;};x'", shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

    try:
        current_connection = subprocess.run(
            f"nmcli -t -f GENERAL.CONNECTION device show {devices[0]} | grep -oP 'GENERAL.CONNECTION:\\K\\w+'",
            shell=True,
            check=True, capture_output=True, text=True)
        devices = list(filter(None, result.stdout.split('\n')))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Getting wifi devices with nmcli failed: {e.stderr}")

    return devices, current_connection


def switch_connections(current_connection, next_connection):
    try:
        command = f"nmcli con down {current_connection}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could shut down the adhoc connection: {e.stderr}")

    try:
        command = f"nmcli con up {next_connection}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli could bring up the adhoc connection: {e.stderr}")
