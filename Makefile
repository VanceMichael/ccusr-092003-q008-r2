
.PHONY: migrate seed test run
migrate:
	python -m scripts.migrate
seed:
	python -m scripts.seed
test:
	python -m pytest -q
run:
	python -m app.main
