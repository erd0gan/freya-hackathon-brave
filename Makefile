.PHONY: sync check lint fmt type test

sync:
	uv sync

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

type:
	uv run mypy

test:
	uv run pytest

check: lint type test
