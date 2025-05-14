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
                tcp=[80, 443],
            ),
            "5678": datatype.Process(
                pid=5678,
                name="python",
                cwd="/home/user/code",
                status="running",
                create_time="1234567891",
                tcp=[8000],
            ),
            "5679": datatype.Process(
                pid=5679,
                name="python",
                cwd="/home/user/code",
                status="running",
                create_time="1234567893",
                tcp=[8005],
            ),
            "9012": datatype.Process(
                pid=9012,
                name="postgres",
                cwd="/var/lib/postgresql",
                status="running",
                create_time="1234567892",
                tcp=[5432],
            ),
            "9013": datatype.Process(
                pid=9013,
                name="dns",
                cwd="/etc/bind",
                status="running",
                create_time="1234567893",
                tcp=[],
                udp=[53],
            ),
        }
        return mock_processes


class LocalProcessMonitor(abstract_provider.AbstractProvider):
    def __init__(self):
        super().__init__()
        self.processes: dict[int, datatype.Process] = {}

    async def get_processes(self) -> dict[str, datatype.Process]:
        connections, udp_connections = get_process_with_openports.get_connections()
        processes = get_process_with_openports.get_processes(connections, udp_connections)
        self.processes = processes
        return {str(k): v for k, v in self.processes.items()}
