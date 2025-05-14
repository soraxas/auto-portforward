import os
import sys

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    import os
    import subprocess

    HAS_PSUTIL = False
    print("psutil not found, using fallback methods")

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


def get_processes(connections: dict[int, list[int]]) -> dict[int, Process]:
    processes = {}

    for pid in connections.keys():
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
                ports=sorted(connections[pid]),
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
                ports=sorted(connections.get(pid, [])),
            )
            processes[p.pid] = p

    return processes


def get_connections(needs_sudo: bool = True, password: str | None = None) -> dict[int, list[int]]:
    """
    Get a mapping of process IDs to listening ports.
    If needs_sudo is True and password is provided, use sudo -S and pass the password via stdin.
    """
    if password is None:
        password = os.getenv("AP_SUDO_PASSWORD")

    connections: dict[int, set[int]] = {}
    if HAS_PSUTIL:
        for c in psutil.net_connections():
            if c.status == "LISTEN":
                container = connections.setdefault(c.pid, set())
                container.add(c.laddr[1])
        return {k: list(v) for k, v in connections.items()}
    else:
        # Fallback using 'lsof' (Unix only)
        try:
            args = []
            if needs_sudo:
                args = ["sudo", "-S"]
                if password is None:
                    raise ValueError(
                        "password is required when needs_sudo is True. Either pass by --password or via $AP_SUDO_PASSWORD environment variable."
                    )
            args += ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]
            if needs_sudo and password is not None:
                output = subprocess.check_output(args, text=True, input=password + "\n")
            else:
                output = subprocess.check_output(args, text=True)
            for line in output.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 9:
                    pid = int(parts[1])
                    port_info = parts[8]
                    if ":" in port_info:
                        port = int(port_info.rsplit(":", 1)[-1])
                        container = connections.setdefault(pid, set())
                        container.add(port)
        except Exception as e:
            print(f"exception: {e}")
            raise e

        return {k: list(v) for k, v in connections.items()}


if __name__ == "__main__":
    connections = get_connections()
    print(connections)
    processes = get_processes(connections)
    print(processes)
