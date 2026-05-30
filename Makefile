.PHONY: help setup test lint format check doctor smoke migrate-current cleanup-dry-run

help:
	@printf '%s\n' \
		'Available targets:' \
		'  make setup          Sync uv environment with dev tools' \
		'  make test           Run pytest' \
		'  make lint           Run ruff check' \
		'  make format         Format with ruff' \
		'  make check          Run format check, lint, and tests' \
		'  make doctor         Verify CLI entrypoints are importable' \
		'  make smoke          Run a no-LMS about:blank smoke harvest' \
		'  make migrate-current Regenerate current-subjects dump format locally' \
		'  make cleanup-dry-run Show rebuildable artifacts cleanup plan'

setup:
	uv sync --extra dev

test:
	uv run --extra dev pytest

lint:
	uv run --extra dev ruff check .

format:
	uv run --extra dev ruff format .

check:
	uv run --extra dev ruff format --check .
	uv run --extra dev ruff check .
	uv run --extra dev pytest

doctor:
	uv run hse-lms-harvest --help >/dev/null
	uv run hse-lms-harvest harvest --help >/dev/null
	uv run hse-lms-harvest cleanup --help >/dev/null
	uv run hse-lms-harvest migrate --help >/dev/null

smoke:
	uv run hse-lms-harvest harvest \
		--url about:blank \
		--profile /tmp/hse-lms-harvest-profile \
		--out /tmp/hse-lms-harvest-smoke \
		--max-pages 1 \
		--headless

migrate-current:
	uv run hse-lms-harvest migrate --out dumps/current-subjects

cleanup-dry-run:
	uv run hse-lms-harvest cleanup --all --out dumps --profile .browser-profile --dry-run
