import json
import logging
import os
import socket
import subprocess
import threading
import signal

from pathlib import Path
from dataclasses import dataclass, field

from auto_portforward.ssh_port_forward import SSHForward
from auto_portforward.utils import preexec_set_pdeathsig
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
    is_finished: threading.Event = field(default_factory=threading.Event)


def run_remote_script(ssh_host: str, shared_memory: SharedMemory, monitor_instance: "RemoteProcessMonitor"):
    import time

    # time.sleep(2)
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
            [
                "ssh",
                "-R",
                f"{port}:localhost:{port}",
                ssh_host,
                f"AP_SUDO_PASSWORD={os.getenv('AP_SUDO_PASSWORD', '')} {remote_cmd}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
            preexec_fn=preexec_set_pdeathsig,
            start_new_session=True,
        )

        # Start threads to monitor stdout/stderr
        def log_output(pipe, prefix):
            for line in pipe:
                if prefix == "stderr":
                    LOGGER.error(f"SSH {prefix}: {line.strip()}")
                else:
                    LOGGER.info(f"SSH {prefix}: {line.strip()}")

        threading.Thread(target=log_output, args=(ssh_process.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=log_output, args=(ssh_process.stderr, "stderr"), daemon=True).start()

        # Accept the connection from the remote process with timeout
        LOGGER.debug("Waiting for remote connection")

        MAX_WAIT_TIME = 30
        local_socket.settimeout(2)
        start_time = time.time()
        while True:
            # Check if SSH process is still alive
            if ssh_process.poll() is not None:
                exit_code = ssh_process.poll()
                raise RuntimeError(f"SSH process died with exit code {exit_code} while waiting for connection")

            if time.time() - start_time > MAX_WAIT_TIME:
                raise RuntimeError("Timeout while waiting for remote connection")
            try:
                conn, _ = local_socket.accept()
                LOGGER.debug("Remote connection established")
                break
            except socket.timeout:
                LOGGER.debug("Still waiting for remote connection...")
                continue

        # Store the connection in the monitor instance, so that we can close it when cleanup is called
        monitor_instance.conn = conn
        monitor_instance.ssh_process = ssh_process
    except Exception as e:
        LOGGER.error(f"Error in setup_connection: {e}", exc_info=True)
        raise

    conn.settimeout(3)

    try:
        last_data: dict[str, datatype.Process] = {}
        while not shared_memory.is_finished.is_set():
            # Read message length (4 bytes)
            try:
                # LOGGER.debug("Reading message length")
                length_bytes = conn.recv(4)
            except socket.timeout:
                continue

            # LOGGER.debug("Received message length: %s", length_bytes)
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
    except socket.error as e:
        # socket is closing
        LOGGER.debug("Socket error in run_remote_script: %s", e)
        pass
    except Exception as e:
        LOGGER.debug("Exception in run_remote_script: %s", e, exc_info=True)
    finally:
        LOGGER.debug("Terminating SSH process")
        ssh_process.send_signal(signal.SIGINT)
        ssh_process.terminate()
        # os.killpg(os.getpgid(ssh_process.pid), signal.SIGTERM)

        try:
            ssh_process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            LOGGER.warning("SSH process did not terminate gracefully, killing...")
            ssh_process.kill()
            ssh_process.wait()
        # LOGGER.debug("Closing local socket")
        # conn.close()
        # local_socket.close()
        # for thread in threads:
        #     thread.cancel()
        #     thread.join()
    LOGGER.debug("Remote socket script finished")


class RemoteProcessMonitor(AbstractProvider):
    def __init__(self, ssh_host: str):
        super().__init__()
        self.ssh_host = ssh_host
        LOGGER.debug("Initializing RemoteProcessMonitor for host: %s", ssh_host)
        self.shared_memory = SharedMemory(processes={})
        self.thread: threading.Thread | None = None
        self.cached_processes: dict[str, datatype.Process] = {}
        self.conn: socket.socket | None = None  # Store the socket connection
        self.ssh_process: subprocess.Popen | None = None
        self.forwarded_ports: dict[int, SSHForward] = {}

    @property
    def name(self) -> str:
        return f"{super().name}: {self.ssh_host}"

    def connect(self) -> bool:
        """Establish SSH connection and socket. Returns True if successful."""
        try:
            self.setup_connection()
            return True
        except Exception as e:
            LOGGER.error("Failed to establish connection: %s", e, exc_info=True)
            return False

    def setup_connection(self):
        self.thread = threading.Thread(target=run_remote_script, args=(self.ssh_host, self.shared_memory, self))
        self.thread.start()

    async def get_processes(self) -> dict[str, datatype.Process]:
        if self.shared_memory.has_new_data.is_set():
            with self.shared_memory.lock:
                self.cached_processes = self.shared_memory.processes
            self.shared_memory.has_new_data.clear()
        return self.cached_processes

    async def cleanup(self) -> None:
        # try to kill the ssh process to speed up cleanup
        if self.ssh_process:
            self.ssh_process.kill()

        LOGGER.debug("Cleaning up RemoteProcessMonitor")
        for port in self.forwarded_ports:
            self.forwarded_ports[port].cleanup()
        self.forwarded_ports.clear()

        with self.shared_memory.lock:
            self.shared_memory.is_finished.set()
        if self.conn:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except Exception as e:
                LOGGER.debug("Error during socket shutdown: %s", e)
            finally:
                self.conn.close()
                self.conn = None

        LOGGER.debug("Closing connection")
        if self.thread:
            self.thread.join()
            self.thread = None

    async def on_ports_turned_on(self, port: int):
        self.forwarded_ports[port] = SSHForward(port, ssh_host=self.ssh_host)
        try:
            # Start the reverse_port subprocess with process group
            self.forwarded_ports[port].start()
        except Exception as e:
            LOGGER.error("Failed to start port forwarding for port %s: %s", port, e)

    async def on_ports_turned_off(self, port: int):
        self.forwarded_ports[port].cleanup()
        self.forwarded_ports.pop(port)
