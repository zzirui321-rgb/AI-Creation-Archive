from abc import ABC, abstractmethod

from ..artifact_store import ArtifactStore
from ..contracts import AdapterOutput, ExecutionRequest


class PreviewProvider(ABC):
    source: str

    @abstractmethod
    async def generate(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput | None:
        raise NotImplementedError
