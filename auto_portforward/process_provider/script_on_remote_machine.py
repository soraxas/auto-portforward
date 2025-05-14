#!/usr/bin/env python3
"""
This is a remote process monitor that sends process and connection information to a local socket.
It is used to monitor processes on a remote machine.

To be run on the remote machine.
"""

import socket
import json
import time
import sys

from dataclasses import asdict

# if we are in ssh_single_file_mode
# we directly inject the get_connections and get_processes functions
# into the local namespace
if not locals().get("ssh_single_file_mode", False):
    from .get_process_with_openports import get_connections, get_processes


def send_via_socket():
    """
    This is a script that is run on the remote machine.
    It sends process and connection information to a local socket.
    It is used to monitor processes on a remote machine.
    To be run on the remote machine.
    """
    if len(sys.argv) != 2:
        print("Usage: python3 remote_monitor.py <port>")
        sys.exit(1)

    port = int(sys.argv[1])
    print(f"Connecting to local socket on port {port}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("localhost", port))
    print("Connected to local socket")

    while True:
        try:
            # Send process and connection information
            connections, udp_connections = get_connections()
            data = {
                "type": "data",
                "processes": {str(k): asdict(v) for k, v in get_processes(connections, udp_connections).items()},
            }
            msg = json.dumps(data).encode()
            length_bytes = len(msg).to_bytes(4, "big")
            print(f"Sending data message, length: {len(msg)}")
            s.sendall(length_bytes + msg)
            time.sleep(1.5)  # Update every second
        except Exception as e:
            import traceback

            print(f"Error in main loop: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            break

    print("Closing connection")
    s.close()


if __name__ == "__main__":
    send_via_socket()
