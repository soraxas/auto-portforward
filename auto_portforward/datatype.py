from dataclasses import dataclass


@dataclass
class Process:
    pid: int
    name: str
    cwd: str
    status: str
    create_time: str
    ports: list[int]
