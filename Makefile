.PHONY: help venv run-api run-ui dev test lint clean

help:
	@echo "Available commands:"
	@echo "  make dev      - Run both API and UI in local development mode"
	@echo "  make run-api  - Run FastAPI backend service"
	@echo "  make run-ui   - Run frontend static web server"
	@echo "  make test     - Run automated unit & security tests"
	@echo "  make clean    - Remove build artifacts and caches"

venv:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt

run-api:
	./start-api.sh

run-ui:
	./start.sh

dev:
	./dev-local.sh

test:
	cd backend && .venv/bin/pytest tests/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf backend/.pytest_cache
