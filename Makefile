.PHONY: install lint fmt typecheck test run-mockops demo

install:
	uv sync

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff check --fix src tests
	uv run ruff format src tests

typecheck:
	uv run mypy

test:
	uv run pytest

run-mockops:
	uv run mockops

demo:
	@echo "demo arrives with milestone M6 (examples/demo.py)"
