from dataclasses import dataclass, field


@dataclass
class Process:
    pid: int
    name: str
    cwd: str
    status: str
    create_time: str
    tcp: list[int] = field(default_factory=list)
    udp: list[int] = field(default_factory=list)
