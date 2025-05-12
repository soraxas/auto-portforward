#!/usr/bin/env python3
"""
This is a remote process monitor that sends process and connection information to a local socket.
It is used to monitor processes on a remote machine.

To be run on the remote machine.
"""

import socket
import psutil
import json
import time
import sys
import logging
import queue

from dataclasses import dataclass, asdict

# Create a queue for logs
log_queue = queue.Queue()


# Custom handler to put logs in queue
class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put(msg)
        except Exception:
            self.handleError(record)


# Configure remote logging
logger = logging.getLogger("remote_monitor")
logger.setLevel(logging.DEBUG)

# Add queue handler
queue_handler = QueueHandler()
queue_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(queue_handler)


def get_processes(connections):
    # print("Fetching process information")
    processes = {}
    # Only iterate through processes that have connections
    for pid in connections.keys():
        try:
            pid = int(pid)
        except ValueError:
            continue
        try:
            proc = psutil.Process(pid)
            p = Process(
                pid=proc.pid,
                name=proc.name(),
                cwd=proc.cwd(),
                status=proc.status(),
                create_time=str(proc.create_time()),
            )
            processes[p.pid] = asdict(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"Error getting process info: {e}")
    # print(f"Found {len(processes)} processes with connections")
    return processes


def get_connections():
    # print("Fetching connection information")
    connections = {}
    for c in psutil.net_connections():
        if c.status == "LISTEN":
            container = connections.setdefault(c.pid, set())
            container.add(c.laddr[1])
    # print(f"Found {len(connections)} processes with listening ports")
    return {str(k): list(v) for k, v in connections.items()}


def main():
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
            connections = get_connections()
            data = {
                "type": "data",
                "processes": get_processes(connections),
                "connections": connections,
            }
            msg = json.dumps(data).encode()
            length_bytes = len(msg).to_bytes(4, "big")
            # print(f"Sending data message, length: {len(msg)}")
            s.sendall(length_bytes + msg)
            time.sleep(1)  # Update every second
        except Exception as e:
            import traceback

            print(f"Error in main loop: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            break

    print("Closing connection")
    s.close()


if __name__ == "__main__":
    main()
