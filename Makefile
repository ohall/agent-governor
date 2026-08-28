.PHONY: install lint fmt typecheck test run-mockops demo bench report

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
	uv run python examples/demo.py --pace 1.0

bench:
	uv run python -m benchmarks.exp1_pn_decay
	uv run python -m benchmarks.exp2_guardrail_latency
	uv run python -m benchmarks.exp3_governance_timeline

report:
	uv run python -m benchmarks.make_report
	@echo "open reports/out/index.html"
