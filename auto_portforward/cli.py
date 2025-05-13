import sys
import logging
import argparse

from .tui import ProcessMonitor

LOGGER = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Process Monitor CLI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--local", action="store_true", help="Use local process monitor")
    group.add_argument("--mock", action="store_true", help="Use mock process monitor")
    parser.add_argument(
        "ssh_host",
        nargs="?",
        default="soraxas@fait",
        help="SSH host (default: soraxas@fait)",
    )
    args = parser.parse_args()

    if args.local:
        from .process_provider.local import LocalProcessMonitor

        monitor = LocalProcessMonitor()
    elif args.mock:
        from .process_provider.local import MockProcessMonitor

        monitor = MockProcessMonitor()
    else:
        from .process_provider.ssh_remote import RemoteProcessMonitor

        monitor = RemoteProcessMonitor(args.ssh_host)
        if not monitor.connect():
            LOGGER.error("Failed to establish connection. Exiting.")
            sys.exit(1)

    app = ProcessMonitor(monitor)
    app.run()


if __name__ == "__main__":
    main()
