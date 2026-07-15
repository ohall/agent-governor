.PHONY: install lint fmt typecheck test run-mockops demo

install:
	uv sync

lint:
	uv run ruff check src tests examples
	uv run ruff format --check src tests examples

fmt:
	uv run ruff check --fix src tests examples
	uv run ruff format src tests examples

typecheck:
	uv run mypy

test:
	uv run pytest

run-mockops:
	uv run mockops

demo:
	@echo "demo arrives with milestone M6 (examples/demo.py)"
