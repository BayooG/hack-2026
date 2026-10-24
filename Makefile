RUN := uv run --env-file .env
N ?= 5

.DEFAULT_GOAL := help
.PHONY: help setup install migrate superuser dev web scheduler test agent slots

help: ## Show this help
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup: install .env migrate ## First-time setup: install, create .env, create the database

install: ## Install dependencies into .venv
	uv venv --allow-existing
	uv pip install -r requirements.txt

# Created on first use from .env.example, with generated secret keys and the
# ANTHROPIC_API_KEY from your shell if it is set. Never overwritten.
define MAKE_ENV
import os, secrets
from cryptography.fernet import Fernet
text = open(".env.example").read()
values = {
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
    "TOKEN_KEY": Fernet.generate_key().decode(),
}
for key, value in values.items():
    text = text.replace(f"\n{key}=\n", f"\n{key}={value}\n")
open(".env", "w").write(text)
print("Created .env with generated secret keys.")
if not values["ANTHROPIC_API_KEY"]:
    print("Add your ANTHROPIC_API_KEY to .env before the agent runs.")
endef
export MAKE_ENV

.env:
	@uv run python -c "$$MAKE_ENV"

migrate: .env ## Create or update the database
	$(RUN) python manage.py migrate

superuser: .env ## Create an admin account for /admin/
	$(RUN) python manage.py createsuperuser

dev: migrate ## Run the web server and the scheduler together (Ctrl+C stops both)
	$(MAKE) -j2 web scheduler

web: .env ## Run the web server on http://localhost:8000
	$(RUN) python manage.py runserver

scheduler: .env ## Run the agent for all users every 5 minutes, and on "Run now"
	$(RUN) python manage.py run_scheduler

test: .env ## Run the tests
	$(RUN) python manage.py test accounts

agent: .env ## CLI: process the newest N emails of the token.json account (make agent N=3)
	$(RUN) python agent.py $(N)

slots: .env ## CLI: show free calendar slots of the token.json account
	$(RUN) python gcal.py
