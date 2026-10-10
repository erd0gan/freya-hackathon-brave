"""Append-only JSONL trace/result store (memo §36)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class JsonlStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, item: BaseModel) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(item.model_dump_json() + "\n")

    def read(self, model: type[T]) -> Iterator[T]:
        if not self.path.exists():
            return
        with self.path.open() as f:
            for line in f:
                if line.strip():
                    yield model.model_validate_json(line)
