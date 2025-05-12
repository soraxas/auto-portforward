import json
import logging
import socket
import subprocess
import select
import threading
import os

try:
    from . import datatype
except ImportError:
    # Fall back to direct import if not in a package
    import datatype

from pathlib import Path

THIS_DIR = Path(__file__).parent


class RemoteProcessMonitor:
    def __init__(self, ssh_host: str):
        self.ssh_host = ssh_host
        self.connections = {}
        self.ssh_process = None
        self.socket = None
        self.conn = None
        self.logger = logging.getLogger("tui.remote_monitor")
        self.logger.debug("Initializing RemoteProcessMonitor for host: %s", ssh_host)

    def connect(self) -> bool:
        """Establish SSH connection and socket. Returns True if successful."""
        try:
            self.setup_connection()
            return True
        except Exception as e:
            self.logger.error("Failed to establish connection: %s", e, exc_info=True)
            return False

    def setup_connection(self):
        try:
            # Create a local socket for communication
            self.logger.debug("Creating local socket")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.bind(("localhost", 0))  # Bind to localhost
            self.socket.listen(1)
            port = self.socket.getsockname()[1]
            self.logger.debug("Created local socket on port %d", port)

            # Read the remote scripte_monitor.py')
            self.logger.debug("Reading remote script from: %s", THIS_DIR)
            with open(THIS_DIR / datatype.__file__, "r") as f:
                remote_script = f.read()

            with open(THIS_DIR / "script_on_remote_machine.py", "r") as f:
                remote_script += "\n" + f.read()

            # Start the remote Python process that will connect back to us
            remote_cmd = f"python3 -c '{remote_script}' {port}"
            self.logger.debug("Starting SSH process with port forwarding")
            self.ssh_process = subprocess.Popen(
                ["ssh", "-R", f"{port}:localhost:{port}", self.ssh_host, remote_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True,
            )

            # Start threads to monitor stdout/stderr
            def log_output(pipe, prefix):
                for line in pipe:
                    self.logger.debug("SSH %s: %s", prefix, line.strip())

            threading.Thread(
                target=log_output, args=(self.ssh_process.stdout, "stdout"), daemon=True
            ).start()
            threading.Thread(
                target=log_output, args=(self.ssh_process.stderr, "stderr"), daemon=True
            ).start()

            # Accept the connection from the remote process
            self.logger.debug("Waiting for remote connection")
            self.conn, _ = self.socket.accept()
            self.logger.debug("Remote connection established")

        except Exception as e:
            self.logger.error("Error in setup_connection: %s", e, exc_info=True)
            raise

    def get_remote_processes(self) -> dict[int, datatype.Process]:
        try:
            # Check if there's data available
            ready = select.select([self.conn], [], [], 0.1)
            if ready[0]:
                # Read message length (4 bytes)
                length_bytes = self.conn.recv(4)
                if not length_bytes:
                    self.logger.debug("Connection closed by remote")
                    return self.last_memory if hasattr(self, "last_memory") else {}
                if len(length_bytes) < 4:
                    self.logger.debug("Partial length bytes received: %s", length_bytes)
                    return self.last_memory if hasattr(self, "last_memory") else {}

                length = int.from_bytes(length_bytes, "big")
                # self.logger.debug("Received message length: %d", length)

                # Read the full message
                data = b""
                remaining = length
                while remaining > 0:
                    chunk = self.conn.recv(min(remaining, 4096))
                    if not chunk:
                        self.logger.debug("Connection closed while reading message")
                        break
                    data += chunk
                    remaining -= len(chunk)
                    # self.logger.debug("Read %d bytes, %d remaining", len(chunk), remaining)

                if len(data) == length:
                    try:
                        info = json.loads(data.decode())
                        if info.get("type") == "log":
                            # Handle log message
                            self.logger.info("Remote: %s", info["message"])
                        elif info.get("type") == "data":
                            # Handle process data
                            self.connections = info["connections"]
                            processes = {
                                int(pid): datatype.Process(**proc)
                                for pid, proc in info["processes"].items()
                            }
                            return processes
                    except json.JSONDecodeError as e:
                        self.logger.error("Error decoding JSON: %s", e)
                        self.logger.debug("Problematic data: %s", data)
                else:
                    self.logger.debug(
                        "Incomplete message: got %d bytes, expected %d",
                        len(data),
                        length,
                    )

        except Exception as e:
            self.logger.error("Error reading from socket: %s", e, exc_info=True)

        return self.last_memory if hasattr(self, "last_memory") else {}

    def cleanup(self):
        self.logger.debug("Cleaning up remote monitor")
        if self.ssh_process:
            self.logger.debug("Terminating SSH process")
            self.ssh_process.terminate()
        if self.conn:
            self.logger.debug("Closing connection")
            self.conn.close()
        if self.socket:
            self.logger.debug("Closing socket")
            self.socket.close()
