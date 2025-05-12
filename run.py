#!/usr/bin/python
from dataclasses import dataclass
import psutil

"""
List all the ports opened by processes on the local machine
to run it with sudo: sudo python port_processes.py
"""

# get all the ports opened by processes on the local machine
# def get_all_ports() -> dict[]:

@dataclass
class Process:
    pid: int
    name: str
    cwd: str
    status: str
    create_time: str

GROUP_BY = 'cwd'

MEMORY: dict[int, Process] = dict()

def update_process_listening_ports():
    # runs regularly to update the status of the process
    connections = {}
    for c in psutil.net_connections():
        if c.status == 'LISTEN':
            container = connections.setdefault(c.pid, set())
            container.add(c.laddr[1])

    for pid, v in connections.items():
        if pid not in MEMORY:
            print('new')
        p = psutil.Process(pid)
        MEMORY[pid] = Process(pid, p.name(), p.cwd(), p.status(), p.create_time())

update_process_listening_ports()

print(MEMORY)
