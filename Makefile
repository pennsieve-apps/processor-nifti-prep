.PHONY: help test unit run clean

SERVICE_NAME ?= processor-nifti-prep
PYTHON ?= python3
export DOCKER_DEFAULT_PLATFORM ?= linux/amd64

.DEFAULT: help

help:
	@echo "Make Help for $(SERVICE_NAME)"
	@echo ""
	@echo "make test   - unit tests, then the processor over data/input/ with Python (no Docker), checking the outputs"
	@echo "make unit   - the pytest unit tests only"
	@echo "make run    - run it in Docker (amd64) over data/input/, writing to data/output/"
	@echo "make clean  - remove data/output/"
	@echo ""
	@echo "data/input/ is gitignored; sample-input/fetch-sample.sh fills it from OpenNeuro."

unit:
	$(PYTHON) -m pytest -q tests

test: clean unit
	@test -d data/input || (echo "data/input/ is empty: run sample-input/fetch-sample.sh first" && exit 1)
	INPUT_DIR=data/input OUTPUT_DIR=data/output $(PYTHON) -m processor.main
	$(PYTHON) tests/check_outputs.py data/output
	@cat data/output/t1w/provenance.json

run: clean
	@test -d data/input || (echo "data/input/ is empty: run sample-input/fetch-sample.sh first" && exit 1)
	docker compose -f docker-compose.yml down --remove-orphans
	docker compose -f docker-compose.yml build
	docker compose -f docker-compose.yml up --exit-code-from processor
	$(PYTHON) tests/check_outputs.py data/output

clean:
	rm -rf data/output
