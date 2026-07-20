.PHONY: check format test

check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/mypy src
	.venv/bin/pytest -q

format:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

test:
	.venv/bin/pytest -q
