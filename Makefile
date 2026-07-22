# CollectRefunds — common tasks.
# New here? Just run:  make start

.PHONY: start secrets dev seed ingest ingest-full worker test migrate revision \
        docker go-live deploy-check install fmt help

# ---------------------------------------------------------------------------
#  THE ONLY COMMAND YOU NEED TO START
#  Installs everything, sets up the database, adds example data, and opens the
#  site. Safe to run more than once.
# ---------------------------------------------------------------------------
start:
	@echo "→ Installing packages..."
	@pip install -q -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "→ Setting up the database..."
	@alembic upgrade head
	@echo "→ Adding example programs..."
	@python scripts/seed.py
	@echo ""
	@echo "✓ Ready. Starting the site at http://localhost:8000"
	@echo "  (Press Ctrl+C to stop. Text codes will print here in this window.)"
	@echo ""
	@uvicorn app.main:app --reload --port 8000

# Generate the two random secrets and print them ready to paste into .env
secrets:
	@echo "Copy these two lines into your .env file:"
	@echo ""
	@python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
	@python -c "import secrets; print('ADMIN_PASSWORD=' + secrets.token_urlsafe(18))"
	@echo ""

# Pull real, current programs from the FTC (no texts sent).
ingest:
	python -m app.ingest.run --limit 5 --no-notify

# Pull everything and send any pending alerts.
ingest-full:
	python -m app.ingest.run

# ---------------------------------------------------------------------------
#  GOING LIVE
#  Checks your settings are safe, then starts everything with Docker + HTTPS.
# ---------------------------------------------------------------------------
go-live: deploy-check
	@echo "→ Settings look good. Starting the live site..."
	docker compose up -d --build
	@echo "✓ Live. It may take a minute for the HTTPS certificate to issue."

# Just the safety check, without starting anything.
deploy-check:
	@python scripts/preflight.py

# ---------------------------------------------------------------------------
#  Everyday helpers
# ---------------------------------------------------------------------------
test:
	python -m pytest tests/ -q

worker:
	python -m app.worker

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

install:
	pip install -r requirements.txt

seed:
	python scripts/seed.py

dev:
	uvicorn app.main:app --reload --port 8000

help:
	@echo "CollectRefunds commands:"
	@echo "  make start        Set up and run the site locally (start here)"
	@echo "  make secrets      Generate the two random passwords for going live"
	@echo "  make ingest       Pull real refund programs from the FTC"
	@echo "  make test         Run the tests"
	@echo "  make go-live      Check settings and launch the live site"
	@echo "  make deploy-check  Check if your settings are safe to launch"
