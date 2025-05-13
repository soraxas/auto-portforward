import json
import logging
import socket
import subprocess
import threading

from pathlib import Path
from dataclasses import dataclass, field
from .abstract_provider import AbstractProvider
from . import get_process_with_openports, script_on_remote_machine
from .. import ROOT_DIR, datatype

THIS_DIR = Path(__file__).parent

LOGGER = logging.getLogger(__name__)


def build_ssh_single_file_mode_script():
    remote_script = 'locals()["ssh_single_file_mode"] = True\n'
    with open(ROOT_DIR / datatype.__file__, "r") as f:
        remote_script += f.read() + "\n"
    with open(THIS_DIR / get_process_with_openports.__file__, "r") as f:
        remote_script += f.read() + "\n"
    with open(THIS_DIR / script_on_remote_machine.__file__, "r") as f:
        remote_script += f.read() + "\n"

    return remote_script


@dataclass
class SharedMemory:
    processes: dict[str, datatype.Process]
    lock: threading.Lock = field(default_factory=threading.Lock)
    has_new_data: threading.Event = field(default_factory=threading.Event)
    is_finished: bool = False


def run_remote_script(ssh_host: str, shared_memory: SharedMemory):
    try:
        # Create a local socket for communication
        LOGGER.debug("Creating local socket")
        local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        local_socket.bind(("localhost", 0))  # Bind to localhost
        local_socket.listen(1)
        port = local_socket.getsockname()[1]
        LOGGER.debug("Created local socket on port %d", port)

        # Read the remote script
        LOGGER.debug("Reading remote script from: %s", THIS_DIR)

        # Start the remote Python process that will connect back to us
        remote_cmd = f"python3 -c '{build_ssh_single_file_mode_script()}' {port}"
        LOGGER.debug("Starting SSH process with port forwarding")
        ssh_process = subprocess.Popen(
            ["ssh", "-R", f"{port}:localhost:{port}", ssh_host, remote_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
        )

        # Start threads to monitor stdout/stderr
        def log_output(pipe, prefix):
            for line in pipe:
                LOGGER.debug(f"SSH {prefix}: {line.strip()}")

        threading.Thread(target=log_output, args=(ssh_process.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=log_output, args=(ssh_process.stderr, "stderr"), daemon=True).start()

        # Accept the connection from the remote process
        LOGGER.debug("Waiting for remote connection")
        conn, _ = local_socket.accept()
        LOGGER.debug("Remote connection established")

        try:
            last_data: dict[str, datatype.Process] = {}
            while not shared_memory.is_finished:
                # Read message length (4 bytes)
                length_bytes = conn.recv(4)
                if not length_bytes:
                    raise RuntimeError("Connection closed by remote")

                length = int.from_bytes(length_bytes, "big")
                # LOGGER.debug("Received message length: %d", length)

                # Read the full message
                data = conn.recv(length)

                try:
                    info = json.loads(data.decode())
                    if info.get("type") == "log":
                        # Handle log message
                        LOGGER.info("Remote: %s", info["message"])
                    elif info.get("type") == "data":
                        # Handle process data
                        new_data = {
                            pid: datatype.Process(
                                pid=proc["pid"],
                                name=proc["name"],
                                cwd=proc["cwd"],
                                status=proc["status"],
                                create_time=proc["create_time"],
                                ports=sorted(proc["ports"]),
                            )
                            for pid, proc in info["processes"].items()
                        }
                        if new_data != last_data:
                            with shared_memory.lock:
                                LOGGER.debug("Setting new data")
                                shared_memory.processes = new_data
                                shared_memory.has_new_data.set()
                            last_data = new_data

                except json.JSONDecodeError as e:
                    LOGGER.error("Error decoding JSON: %s", e)
                    LOGGER.debug("Problematic data: %s", data)
        finally:
            LOGGER.debug("Terminating SSH process")
            ssh_process.terminate()
            LOGGER.debug("Closing local socket")
            conn.close()
            local_socket.close()
            # for thread in threads:
            #     thread.cancel()
            #     thread.join()

    except Exception as e:
        LOGGER.error(f"Error in setup_connection: {e}", exc_info=True)
        raise


class RemoteProcessMonitor(AbstractProvider):
    def __init__(self, ssh_host: str):
        self.ssh_host = ssh_host
        LOGGER.debug("Initializing RemoteProcessMonitor for host: %s", ssh_host)
        self.shared_memory = SharedMemory(processes={})
        self.thread: threading.Thread | None = None
        self.cached_processes: dict[str, datatype.Process] = {}

    def connect(self) -> bool:
        """Establish SSH connection and socket. Returns True if successful."""
        try:
            self.setup_connection()
            return True
        except Exception as e:
            LOGGER.error("Failed to establish connection: %s", e, exc_info=True)
            return False

    def setup_connection(self):
        self.thread = threading.Thread(target=run_remote_script, args=(self.ssh_host, self.shared_memory))
        self.thread.start()

    async def get_processes(self) -> dict[str, datatype.Process]:
        if self.shared_memory.has_new_data.is_set():
            with self.shared_memory.lock:
                self.cached_processes = self.shared_memory.processes
            self.shared_memory.has_new_data.clear()
        return self.cached_processes

    async def cleanup(self) -> None:
        self.shared_memory.is_finished = True
        if self.thread:
            self.thread.join()
