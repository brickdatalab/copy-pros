.PHONY: test lint typecheck run-dry

test:
	pytest -q

lint:
	ruff check trader tests

typecheck:
	mypy trader

run-dry:
	python scripts/run_event_bot.py --event "$${EVENT}" --mode dry_run
