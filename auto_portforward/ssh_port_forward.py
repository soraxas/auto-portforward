import subprocess
import logging

import os
import signal

LOGGER = logging.getLogger(__file__)


def set_process_group():
    """Set the process group ID to the current process ID."""
    os.setpgrp()


class SSHForward:
    def __init__(self, port: int):
        self.port = port
        self.had_cleanup = False
        self.process: subprocess.Popen | None = None

    def start(self):
        self.process = subprocess.Popen(
            ["ssh", "-N", "-L", f"{self.port}:localhost:{self.port}", "fait"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=set_process_group,
        )
        LOGGER.info(
            "Started port forwarding for port %s with PID %s",
            self.port,
            self.process.pid,
        )

    def cleanup(self):
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
        self.cleanup = True

    def __del__(self):
        """Clean up SSH process when object is destroyed."""
        if not self.had_cleanup:
            self.cleanup()
