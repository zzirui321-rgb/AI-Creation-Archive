from abc import ABC, abstractmethod

from ..artifact_store import ArtifactStore
from ..contracts import AdapterOutput, ExecutionRequest


class BaseAdapter(ABC):
    version = "1"

    @abstractmethod
    async def execute(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput:
        raise NotImplementedError
