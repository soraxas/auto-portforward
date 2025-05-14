import sys
import logging
import argparse

from auto_portforward.tui import ProcessMonitor

LOGGER = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Process monitoring and port forwarding tool. Monitors processes with open ports "
            "and allows toggling port forwarding for individual processes or groups."
            "When monitoring a remote SSH host, ports can be automatically forwarded. "
            "When targeting a process group, port forwarding is automatically managed as "
            "processes enter or leave the group - new processes in the group will have "
            "their ports forwarded, and forwarding is removed when processes exit."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--local", action="store_true", help="Use local process monitor")
    group.add_argument("--mock", action="store_true", help="Use mock process monitor")
    parser.add_argument(
        "ssh_host",
        nargs="?",
        help="SSH host to connect to (for remote process monitor and portforwarding)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    if args.verbose:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

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
