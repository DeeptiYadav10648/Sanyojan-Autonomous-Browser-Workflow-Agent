.DEFAULT_GOAL := help

UV ?= uv
NPM ?= npm
export PLAYWRIGHT_BROWSERS_PATH ?= 0
ifeq ($(OS),Windows_NT)
PYTHON ?= .venv/Scripts/python.exe
else
PYTHON ?= .venv/bin/python
endif

.PHONY: help setup browser backend dev build test deploy stop logs

help:
	@echo "setup: install locked dependencies, Chromium, and a missing .env"
	@echo "backend / dev: run each in a separate terminal; build: build dashboard"
	@echo "test: offline project checks; deploy / stop / logs: Docker Compose"

setup:
	$(UV) sync --frozen
	"$(PYTHON)" -m playwright install chromium
	$(NPM) ci
	"$(PYTHON)" -c "from pathlib import Path; p = Path('.env'); p.exists() or p.write_bytes(Path('.env.example').read_bytes())"

browser:
	"$(PYTHON)" -m playwright install chromium

backend:
	"$(PYTHON)" -m uvicorn sanyojan.app:app --app-dir backend --host 127.0.0.1 --port 8000

dev:
	$(NPM) run dev

build:
	$(NPM) run build

test:
	$(NPM) test

deploy:
	docker compose up --build -d

stop:
	docker compose down

logs:
	docker compose logs -f sanyojan
