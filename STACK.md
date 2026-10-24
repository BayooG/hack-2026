# Tech Stack

An email-triage agent with a small web app around it. It reads Gmail, classifies and labels each email with Claude, drafts replies, and blocks time on Google Calendar. This file describes what it is built with.

## Summary

| Layer | Choice |
| --- | --- |
| Language | Python 3.13 |
| Package manager | `uv` (plus a `Makefile` for common tasks) |
| Backend | Django 6.1 (WSGI) |
| Frontend | Server-rendered Django templates, one hand-written CSS file |
| Database | SQLite (`app.db`, WAL mode) |
| AI | Anthropic SDK, `claude-sonnet-5-5`, pydantic structured output |
| Integrations | Gmail API, Google Calendar API, Google OAuth |
| Background work | Custom `run_scheduler` management command |
| Tests | Django `TestCase` + `unittest.mock` |
| Deployment | Local development only, for now |

Versions are the ones installed in `.venv`. `requirements.txt` doesn't pin any.

## Language and tooling

- Python 3.13, in a `.venv` created by `uv venv`.
- Dependencies are installed with `uv pip install -r requirements.txt`. The file lists `anthropic`, `pydantic`, `google-api-python-client`, `google-auth-oauthlib`, `django`, `cryptography` and `requests`.
- Secrets live in `.env` (template: `.env.example`) and are loaded with `uv run --env-file .env`.
- `Makefile` targets:
  - `make setup`, `make install`, `make migrate`, `make superuser`
  - `make dev` runs the web server and the scheduler together
  - `make test`
  - `make agent` runs the agent from the CLI
  - `make slots` shows free calendar slots

## Backend

- Django, configured in `config/settings.py`, with one local app, `accounts`.
- Custom email-based user model (`accounts.User`) with session auth. Signup and login forms are in `accounts/forms.py`.
- Django admin is enabled at `/admin/`.
- `/health` returns `{"status": "ok"}`.
- The agent code is flat modules at the repo root:
  - `agent.py` is the classifier, reply drafter and tool-running agent
  - `actions.py` holds the extra tools the agent can call
  - `gmail.py` and `gcal.py` wrap the Google APIs
  - `google_auth.py`, `labels.py` and `run_context.py` hold auth, label loading and per-user run state
- `accounts/runner.py` connects the Django models to the agent.

## Frontend

- Django templates in `accounts/templates/accounts/`: landing page, login, signup and dashboard, plus small partials.
- A single stylesheet, `accounts/static/accounts/app.css`, built on CSS custom properties with a neutral palette and an indigo accent.
- Inter font from Google Fonts.
- No JavaScript framework, bundler or npm. There is one small inline script in `base.html`.

## Database

- SQLite file `app.db`, in WAL mode with immediate transactions. The web server and the scheduler share it.
- Models in `accounts/models.py`:
  - `User`
  - `GoogleConnection` stores the Google tokens
  - `ProcessedEmail` records each email the agent handled, with its labels

## AI and agent

- `anthropic` SDK, model `claude-sonnet-5-5` (set in `agent.py`).
- **Classification:** one `messages.parse` call per email. A dynamic pydantic model returns up to 3 labels with sub-labels, an urgency, the minutes needed, whether a reply is needed, and a summary.
- **Label rules:** `labels.md` describes each label to the classifier.
- **Reply drafting:** a separate call guided by `skills/write-reply/SKILL.md`. Replies are saved as Gmail drafts and never sent.
- **Extra actions:** `beta.messages.tool_runner` with tools defined through `anthropic.beta_tool` in `actions.py`.
- No vector store or RAG.

## Integrations

- **Gmail** (`gmail.py`): reads mail, applies labels, creates drafts. Calls retry on quota and server errors.
- **Google Calendar** (`gcal.py`): finds free slots and blocks time for replies.
- **OAuth scopes:** `gmail.modify`, `calendar.readonly`, `calendar.events`.
- **Two OAuth flows:** the CLI uses `InstalledAppFlow` with `credentials.json` and `token.json`. The web app uses a web-application flow in `accounts/google_connect.py`, with `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
- **Token storage:** refresh tokens are encrypted with Fernet (`cryptography`) using `TOKEN_KEY`.

## Scheduling

- No Celery or cron. `accounts/management/commands/run_scheduler.py` is a loop that polls every 10 seconds.
- It runs the agent every 5 minutes for each user who has the agent enabled and Google connected.
- A user can request an immediate run with "Run now".

## Testing

- Django `TestCase` with `unittest.mock`, in `accounts/tests.py`. Run with `make test`.
- pytest, pytest-django and ruff are installed locally but aren't declared or configured in the repo.

## Deployment

- Local development only. There is no Dockerfile, CI or production config.
- `DEBUG` defaults to on and `ALLOWED_HOSTS` is limited to localhost.
