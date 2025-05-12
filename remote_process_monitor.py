import json
import logging
import socket
import subprocess
import select
import threading
import os
import asyncio
from dataclasses import dataclass


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
        self.loop = asyncio.get_event_loop()
        self.reader = None
        self.writer = None

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

            # Read the remote script
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
                    self.logger.debug(f"SSH {prefix}: {line.strip()}")

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

            # Create StreamReader and StreamWriter
            self.reader = asyncio.StreamReader()
            transport, protocol = self.loop.run_until_complete(
                self.loop.create_connection(
                    lambda: asyncio.StreamReaderProtocol(self.reader), sock=self.conn
                )
            )
            self.writer = asyncio.StreamWriter(
                transport, protocol, self.reader, self.loop
            )

        except Exception as e:
            self.logger.error(f"Error in setup_connection: {e}", exc_info=True)
            raise

    async def get_remote_processes(self) -> dict[int, datatype.Process]:
        try:
            # Read message length (4 bytes)
            length_bytes = await self.reader.readexactly(4)
            if not length_bytes:
                self.logger.debug("Connection closed by remote")
                return self.last_memory if hasattr(self, "last_memory") else {}

            length = int.from_bytes(length_bytes, "big")
            # self.logger.debug("Received message length: %d", length)

            # Read the full message
            data = await self.reader.readexactly(length)

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
                    self.last_memory = processes
                    return processes
            except json.JSONDecodeError as e:
                self.logger.error("Error decoding JSON: %s", e)
                self.logger.debug("Problematic data: %s", data)

        except asyncio.IncompleteReadError:
            self.logger.debug("Connection closed while reading")
        except Exception as e:
            self.logger.error("Error reading from socket: %s", e, exc_info=True)

        return self.last_memory if hasattr(self, "last_memory") else {}

    def cleanup(self):
        self.logger.debug("Cleaning up remote monitor")
        if self.ssh_process:
            self.logger.debug("Terminating SSH process")
            self.ssh_process.terminate()
        if self.writer:
            self.logger.debug("Closing writer")
            self.loop.run_until_complete(self.writer.drain())
            self.writer.close()
            self.loop.run_until_complete(self.writer.wait_closed())
        if self.socket:
            self.logger.debug("Closing socket")
            self.socket.close()
