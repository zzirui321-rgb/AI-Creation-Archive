import asyncio

from ..artifact_store import ArtifactStore
from ..contracts import AdapterOutput, ExecutionRequest
from .base import PreviewProvider


class PrewarmedProvider(PreviewProvider):
    source = "prewarmed_cache"

    async def generate(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput | None:
        return await asyncio.to_thread(store.load_cache, request, self.source)
