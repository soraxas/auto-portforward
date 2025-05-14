import subprocess
import logging

import os
import signal
import atexit

from auto_portforward.utils import preexec_set_pdeathsig

LOGGER = logging.getLogger(__file__)


class SSHForward:
    def __init__(self, port: int, ssh_host: str):
        # port to forward
        self.port = port
        # ssh host to connect to
        self.ssh_host = ssh_host
        # whether the cleanup has been called
        self.had_cleanup = False
        # the process that is running the port forwarding
        self.process: subprocess.Popen | None = None
        # register cleanup to be called when the program exits
        atexit.register(self.cleanup)

    def start(self):
        self.process = subprocess.Popen(
            ["ssh", "-N", "-L", f"{self.port}:localhost:{self.port}", self.ssh_host],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=preexec_set_pdeathsig,
        )
        LOGGER.info(
            "Started port forwarding for port %s with PID %s",
            self.port,
            self.process.pid,
        )

    def cleanup(self):
        if self.had_cleanup:
            return
        try:
            self.process.terminate()
            os.kill(self.process.pid, signal.SIGINT)
            # # Kill the entire process group
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process.wait(timeout=5)
            LOGGER.info("Terminated port forwarding for port %s", self.port)
        except subprocess.TimeoutExpired:
            LOGGER.warning(
                "Port forwarding process for port %s did not terminate gracefully, forcing...",
                self.port,
            )
            os.kill(self.process.pid, signal.SIGKILL)
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except Exception as e:
            LOGGER.error("Error terminating port forwarding for port %s: %s", self.port, e)
        self.had_cleanup = True
