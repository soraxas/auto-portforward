from abc import ABC, abstractmethod

from auto_portforward import datatype


class AbstractProvider(ABC):
    @abstractmethod
    async def get_processes(self) -> dict[str, datatype.Process]:
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        pass
