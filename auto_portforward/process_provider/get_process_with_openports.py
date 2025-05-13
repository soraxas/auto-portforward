import psutil

# if we are in ssh_single_file_mode
# we directly inject the Process class
# into the local namespace
if not locals().get("ssh_single_file_mode", False):
    from ..datatype import Process


def get_processes(connections: dict[int, list[int]]) -> dict[int, Process]:
    # print("Fetching process information")
    processes = {}
    # Only iterate through processes that have connections
    for pid in connections.keys():
        try:
            pid = int(pid)
        except (ValueError, TypeError):
            continue
        try:
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
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"Error getting process info: {e}")
    # print(f"Found {len(processes)} processes with connections")
    return processes


def get_connections() -> dict[int, list[int]]:
    # print("Fetching connection information")
    connections: dict[int, set[int]] = {}
    for c in psutil.net_connections():
        if c.status == "LISTEN":
            container = connections.setdefault(c.pid, set())
            container.add(c.laddr[1])
    # print(f"Found {len(connections)} processes with listening ports")
    return {k: list(v) for k, v in connections.items()}
