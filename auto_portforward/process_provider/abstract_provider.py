import logging

from abc import ABC, abstractmethod
from typing import Set


from auto_portforward import datatype

LOGGER = logging.getLogger(__file__)


class AbstractProvider(ABC):
    def __init__(self):
        self.toggled_ports: Set[int] = set()

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def get_processes(self) -> dict[str, datatype.Process]:
        pass

    async def cleanup(self) -> None:
        for port in self.toggled_ports:
            await self.on_ports_turned_off(port)
        self.toggled_ports.clear()

    async def on_ports_turned_on(self, port: int):
        LOGGER.info("Port %i is turned on", port)

    async def on_ports_turned_off(self, port: int):
        LOGGER.info("Port %i is turned off", port)

    async def set_toggled_ports(self, ports: Set[int]) -> None:
        """
        This method is used to just manage ports on-off event.
        """

        # Remove old port forwards
        for existing in list(self.toggled_ports):
            if existing not in ports:
                await self.on_ports_turned_off(existing)
                self.toggled_ports.remove(existing)
        # Start new port forwards
        for p in ports:
            if p not in list(self.toggled_ports):
                await self.on_ports_turned_on(p)
                self.toggled_ports.add(p)
