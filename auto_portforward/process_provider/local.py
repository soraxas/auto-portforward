from auto_portforward import datatype
from . import abstract_provider
from . import get_process_with_openports


class MockProcessMonitor(abstract_provider.AbstractProvider):
    def __init__(self):
        super().__init__()
        self.processes: dict[int, datatype.Process] = {}

    async def get_processes(self) -> dict[str, datatype.Process]:
        # Create some mock processes with listening ports
        mock_processes = {
            "1234": datatype.Process(
                pid=1234,
                name="nginx",
                cwd="/etc/nginx",
                status="running",
                create_time="1234567890",
                ports=[80, 443],
            ),
            "5678": datatype.Process(
                pid=5678,
                name="python",
                cwd="/home/user/code",
                status="running",
                create_time="1234567891",
                ports=[8000],
            ),
            "9012": datatype.Process(
                pid=9012,
                name="postgres",
                cwd="/var/lib/postgresql",
                status="running",
                create_time="1234567892",
                ports=[5432],
            ),
        }
        return mock_processes


class LocalProcessMonitor(abstract_provider.AbstractProvider):
    def __init__(self):
        super().__init__()
        self.processes: dict[int, datatype.Process] = {}

    async def get_processes(self) -> dict[str, datatype.Process]:
        connections = get_process_with_openports.get_connections()
        processes = get_process_with_openports.get_processes(connections)
        self.processes = processes
        return {str(k): v for k, v in self.processes.items()}
