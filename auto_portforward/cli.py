import sys
import logging

from .process_provider.local import MockProcessMonitor
from .tui import ProcessMonitor

LOGGER = logging.getLogger(__name__)


def main():
    ssh_host = sys.argv[1] if len(sys.argv) > 1 else "soraxas@fait"
    LOGGER.debug("Starting TUI with SSH host: %s", ssh_host)

    # First establish the connection
    # monitor = RemoteProcessMonitor(ssh_host)
    # if not monitor.connect():
    #     LOGGER.error("Failed to establish connection. Exiting.")
    #     sys.exit(1)
    # monitor = LocalProcessMonitor()
    monitor = MockProcessMonitor()

    app = ProcessMonitor(monitor)
    app.run()


if __name__ == "__main__":
    main()
