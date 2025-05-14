import sys
import logging
import argparse

from auto_portforward.tui import ProcessMonitor

LOGGER = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Process Monitor CLI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--local", action="store_true", help="Use local process monitor")
    group.add_argument("--mock", action="store_true", help="Use mock process monitor")
    parser.add_argument(
        "ssh_host",
        nargs="?",
        help="SSH host to connect to (for remote process monitor and portforwarding)",
    )
    args = parser.parse_args()

    if args.local:
        from auto_portforward.process_provider.local import LocalProcessMonitor

        monitor = LocalProcessMonitor()
    elif args.mock:
        from auto_portforward.process_provider.local import MockProcessMonitor

        monitor = MockProcessMonitor()
    else:
        if not args.ssh_host:
            parser.error("SSH host is required when not using local or mock process monitor")
            sys.exit(1)

        from auto_portforward.process_provider.ssh_remote import RemoteProcessMonitor

        monitor = RemoteProcessMonitor(args.ssh_host)
        if not monitor.connect():
            LOGGER.error("Failed to establish connection. Exiting.")
            sys.exit(1)

    app = ProcessMonitor(monitor)
    app.run()


if __name__ == "__main__":
    main()
