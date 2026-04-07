.PHONY: run build stop restart logs shell setup local

# ── Docker (recommended) ────────────────────────────────────────────────────

## Build the Docker image
build:
	docker compose build

## Start the app (builds if needed, runs in background)
run:
	@[ -f .env ] || { echo "No .env found — run 'make setup' first"; exit 1; }
	docker compose up --build -d
	@echo ""
	@echo "App starting at http://localhost:8501"
	@echo "Run 'make logs' to follow startup (Ollama model pull may take a few minutes on first run)"

## Stop the app
stop:
	docker compose down

## Restart the app
restart:
	docker compose restart

## Follow container logs
logs:
	docker compose logs -f

## Open a shell inside the running container
shell:
	docker compose exec app bash

# ── Local (no Docker) ───────────────────────────────────────────────────────

## Install Python dependencies
install:
	pip install -r requirements.txt

## Run without Docker (Ollama must already be running: ollama serve)
local:
	@[ -f .env ] || { echo "No .env found — run 'make setup' first"; exit 1; }
	streamlit run app.py

# ── Setup ───────────────────────────────────────────────────────────────────

## Create .env from template (only if .env doesn't exist yet)
setup:
	@[ -f .env ] && echo ".env already exists, skipping." || (cp .env.example .env && echo ".env created — fill in your keys before running.")
