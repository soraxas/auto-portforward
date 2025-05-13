import json
import logging
import socket
import subprocess
import threading
import asyncio

from pathlib import Path

from .abstract_provider import AbstractProvider
from .. import ROOT_DIR, datatype

THIS_DIR = Path(__file__).parent

LOGGER = logging.getLogger(__name__)


class RemoteProcessMonitor(AbstractProvider):
    def __init__(self, ssh_host: str):
        self.ssh_host = ssh_host
        self.connections: dict[str, list[int]] = {}
        self.ssh_process: subprocess.Popen | None = None
        self.socket = None
        self.conn = None
        LOGGER.debug("Initializing RemoteProcessMonitor for host: %s", ssh_host)
        self.loop = asyncio.get_event_loop()
        self.reader: asyncio.StreamReader | None = None
        self.writer = None
        self.last_memory: dict[str, datatype.Process] = {}

    def connect(self) -> bool:
        """Establish SSH connection and socket. Returns True if successful."""
        try:
            self.setup_connection()
            return True
        except Exception as e:
            LOGGER.error("Failed to establish connection: %s", e, exc_info=True)
            return False

    def setup_connection(self):
        try:
            # Create a local socket for communication
            LOGGER.debug("Creating local socket")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.bind(("localhost", 0))  # Bind to localhost
            self.socket.listen(1)
            port = self.socket.getsockname()[1]
            LOGGER.debug("Created local socket on port %d", port)

            # Read the remote script
            LOGGER.debug("Reading remote script from: %s", THIS_DIR)
            with open(ROOT_DIR / datatype.__file__, "r") as f:
                remote_script = f.read()
            with open(THIS_DIR / "script_on_remote_machine.py", "r") as f:
                remote_script += "\n" + f.read()

            # Start the remote Python process that will connect back to us
            remote_cmd = f"python3 -c '{remote_script}' {port}"
            LOGGER.debug("Starting SSH process with port forwarding")
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
                    LOGGER.debug(f"SSH {prefix}: {line.strip()}")

            threading.Thread(target=log_output, args=(self.ssh_process.stdout, "stdout"), daemon=True).start()
            threading.Thread(target=log_output, args=(self.ssh_process.stderr, "stderr"), daemon=True).start()

            # Accept the connection from the remote process
            LOGGER.debug("Waiting for remote connection")
            self.conn, _ = self.socket.accept()
            LOGGER.debug("Remote connection established")

            # Create StreamReader and StreamWriter
            self.reader = asyncio.StreamReader()
            transport, protocol = self.loop.run_until_complete(
                self.loop.create_connection(lambda: asyncio.StreamReaderProtocol(self.reader), sock=self.conn)
            )
            self.writer = asyncio.StreamWriter(transport, protocol, self.reader, self.loop)

        except Exception as e:
            LOGGER.error(f"Error in setup_connection: {e}", exc_info=True)
            raise

    async def get_processes(self) -> dict[str, datatype.Process]:
        if not self.reader:
            raise RuntimeError("Reader not initialized")
        try:
            # Read message length (4 bytes)
            length_bytes = await self.reader.readexactly(4)
            if not length_bytes:
                LOGGER.debug("Connection closed by remote")
                return self.last_memory

            length = int.from_bytes(length_bytes, "big")
            # LOGGER.debug("Received message length: %d", length)

            # Read the full message
            data = await self.reader.readexactly(length)

            try:
                info = json.loads(data.decode())
                if info.get("type") == "log":
                    # Handle log message
                    LOGGER.info("Remote: %s", info["message"])
                elif info.get("type") == "data":
                    # Handle process data
                    self.last_memory = {pid: datatype.Process(**proc) for pid, proc in info["processes"].items()}
                    return self.last_memory
            except json.JSONDecodeError as e:
                LOGGER.error("Error decoding JSON: %s", e)
                LOGGER.debug("Problematic data: %s", data)

        except asyncio.IncompleteReadError as e:
            LOGGER.error("Connection closed while reading")
            raise e
        except Exception as e:
            LOGGER.error("Error reading from socket: %s", e, exc_info=True)
            raise e

        return self.last_memory

    async def cleanup(self) -> None:
        LOGGER.debug("Cleaning up remote monitor")
        if self.ssh_process:
            LOGGER.debug("Terminating SSH process")
            self.ssh_process.terminate()
        if self.writer:
            LOGGER.debug("Closing writer")
            await self.writer.drain()
            self.writer.close()
            await self.writer.wait_closed()
        if self.socket:
            LOGGER.debug("Closing socket")
            self.socket.close()
