"""领域工具使用的检索契约，不依赖向量库或具体业务。"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SearchFilter:
    """所有条件按 AND 组合；数值上限包含边界。"""

    equals: dict[str, str | int | bool] = field(default_factory=dict)
    less_than_or_equal: dict[str, float] = field(default_factory=dict)


@dataclass
class SearchHit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float


class SearchService(Protocol):
    def search(
        self,
        collection: str,
        query: str,
        filters: SearchFilter | None = None,
        top_k: int = 5,
    ) -> list[SearchHit]:
        """返回按相关性排序的结果；无命中返回空列表，失败抛出异常。"""
        ...
