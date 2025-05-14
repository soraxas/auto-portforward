import os
import socket
import sys
import logging

LOGGER = logging.getLogger(__name__)

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    import os
    import subprocess

    HAS_PSUTIL = False
    LOGGER.warning("psutil not found, using fallback methods")

# If we are in ssh_single_file_mode
# we directly inject the Process class
# into the local namespace
if not locals().get("ssh_single_file_mode", False):
    from ..datatype import Process


def get_cwd_linux(pid: int) -> str:
    try:
        password = os.getenv("AP_SUDO_PASSWORD")
        if password:
            cmd = ["sudo", "-S", "readlink", f"/proc/{pid}/cwd"]
            result = subprocess.run(cmd, input=password + "\n", capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        else:
            return os.readlink(f"/proc/{pid}/cwd")
    except Exception:
        pass
    return "?"


def get_cwd_macos(pid: int) -> str:
    try:
        result = subprocess.check_output(["lsof", "-a", "-p", str(pid), "-d", "cwd"], text=True)
        lines = result.strip().split("\n")
        if len(lines) >= 2:
            # Last column of the second line is the cwd
            return lines[1].split()[-1]
    except Exception:
        pass
    return "?"


def get_cwd_fallback(pid: int) -> str:
    if sys.platform.startswith("linux"):
        return get_cwd_linux(pid)
    elif sys.platform.startswith("darwin"):
        return get_cwd_macos(pid)
    else:
        return "?"


def get_processes(connections: dict[int, list[int]], udp_connections: dict[int, list[int]]) -> dict[int, Process]:
    processes = {}

    for pid in connections.keys() | udp_connections.keys():
        try:
            pid = int(pid)
        except (ValueError, TypeError):
            continue

        if HAS_PSUTIL:
            proc = psutil.Process(pid)
            p = Process(
                pid=proc.pid,
                name=proc.name(),
                cwd=proc.cwd(),
                status=proc.status(),
                create_time=str(proc.create_time()),
                tcp=sorted(connections.get(pid, [])),
                udp=sorted(udp_connections.get(pid, [])),
            )
            processes[p.pid] = p

        else:
            # Fallback: basic info using single ps command
            # Get process name, status, and start time (no cwd available)
            cmd = ["ps", "-p", str(pid), "-o", "comm=,stat=,lstart="]
            output = subprocess.check_output(cmd, text=True).strip()
            if not output:
                continue

            parts = output.split()
            name = parts[0]
            status = parts[1]
            create_time = " ".join(parts[2:])

            p = Process(
                pid=pid,
                name=name,
                cwd=get_cwd_fallback(pid),
                status=status,
                create_time=create_time,
                tcp=sorted(connections.get(pid, [])),
                udp=sorted(udp_connections.get(pid, [])),
            )
            processes[p.pid] = p

    return processes


def get_connections(sudo_password: str | None = None) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """
    Get a mapping of process IDs to listening ports for both TCP and UDP.
    If needs_sudo is True and sudo_password is provided, use sudo -S and pass the password via stdin.
    Returns a tuple of (tcp_connections, udp_connections)
    """
    sudo_password = sudo_password or os.getenv("AP_SUDO_PASSWORD")

    tcp_connections: dict[int, set[int]] = {}
    udp_connections: dict[int, set[int]] = {}

    def mapper(connections: dict[int, set[int]]) -> dict[int, list[int]]:
        return {k: list(v) for k, v in connections.items()}

    if HAS_PSUTIL:
        for c in psutil.net_connections():
            if c.status == "LISTEN":
                container = tcp_connections.setdefault(c.pid, set())
                container.add(c.laddr[1])
            elif c.type == socket.SOCK_DGRAM:
                container = udp_connections.setdefault(c.pid, set())
                container.add(c.laddr[1])
        return mapper(tcp_connections), mapper(udp_connections)
    else:
        # Fallback using 'lsof' (Unix only)
        try:
            args = []
            if sudo_password:
                args = ["sudo", "-S"]
            args += [
                "lsof",
                "-nP",
                "-iTCP",
                "-sTCP:LISTEN",
            ]
            if sudo_password is not None:
                output = subprocess.check_output(args, text=True, input=sudo_password + "\n")
            else:
                output = subprocess.check_output(args, text=True)
            for line in output.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 9:
                    continue
                pid = int(parts[1])
                port_info = parts[8]
                if ":" not in port_info:
                    continue
                port_str = port_info.rsplit(":", 1)[-1]
                if not port_str.isdigit():
                    continue
                port = int(port_str)
                proto = parts[7]
                if proto.startswith("TCP") and "LISTEN" in line:
                    container = tcp_connections.setdefault(pid, set())
                    container.add(port)
                elif proto.startswith("UDP"):
                    container = udp_connections.setdefault(pid, set())
                    container.add(port)
        except Exception as e:
            LOGGER.error(f"exception: {e}")
            raise e

    return mapper(tcp_connections), mapper(udp_connections)


if __name__ == "__main__":
    connections, udp_connections = get_connections()
    print("tcp connections", connections)
    print("udp connections", udp_connections)
    processes = get_processes(connections, udp_connections)
    print("processes", processes)
