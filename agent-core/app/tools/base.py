from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any

from app.schemas import ToolSpec


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    @abstractmethod
    def run(self, **kwargs: Any) -> str:
        raise NotImplementedError

    async def arun(self, **kwargs: Any) -> str:
        """Async entry point used by the registry.

        Default implementation delegates to the synchronous ``run``; tools that
        need ``await`` (e.g. MCP-backed tools) can override this method
        directly and leave ``run`` as a thin wrapper.
        """
        result = self.run(**kwargs)
        if inspect.isawaitable(result):
            return await result  # type: ignore[return-value]
        return result
