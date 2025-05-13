import sys
import logging

from auto_portforward.remote_process_monitor import RemoteProcessMonitor
from auto_portforward.tui import ProcessMonitor

LOGGER = logging.getLogger(__name__)


def main():
    ssh_host = sys.argv[1] if len(sys.argv) > 1 else "soraxas@fait"
    LOGGER.debug("Starting TUI with SSH host: %s", ssh_host)

    # First establish the connection
    remote_monitor = RemoteProcessMonitor(ssh_host)
    if not remote_monitor.connect():
        LOGGER.error("Failed to establish connection. Exiting.")
        sys.exit(1)

    try:
        app = ProcessMonitor(remote_monitor)
        app.run()
    finally:
        remote_monitor.cleanup()


if __name__ == "__main__":
    main()
