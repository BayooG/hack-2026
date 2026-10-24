# Per-user Labels: Spec

Oct 24, 2026 · @Ryan · Status: v2 built

## Overview

Every web user gets the same label descriptions today: `labels.py` reads the repo's `labels.md`. Other users have different Gmail labels, so the classifier sees descriptions that don't match their setup.

This change gives each user their own labels, stored in the database and edited on a Labels tab. Each label has five fields: emoji, name, sub-label rule, what belongs, and what doesn't belong. New users start with the labels from `labels.md`. The agent reads the running user's labels; the CLI keeps reading `labels.md`.

### What changed from v1

v1 (built, not committed) stored the raw markdown in `User.labels_md` and edited it in a textarea on a separate `/labels/` page, linked from the bottom of the Settings card. Feedback: the page was hard to find, the labels should be prefilled from `labels.md`, and the editor should have one field per part of a label. v2 replaces the textarea with a structured editor on its own Labels tab, and stores one row per label instead of markdown.

## Goals and non-goals

Goals:

- The labels have their own tab, with one row per label and one field per part.
- New users, and existing users with no labels yet, start with the labels from `labels.md`.
- The user sees which of their labels Gmail doesn't have, and which Gmail labels they haven't described.

Non-goals (first version):

- Creating, renaming or deleting Gmail labels from the Labels tab. Labels still come from Gmail; the rows only describe them.
- Drag-and-drop reordering. Rows keep the order they were added in.
- Edit history.

## Data

New model `Label` in `accounts`, one row per label per user:

| Field | Type | Notes |
| ----- | ---- | ----- |
| `user` | FK User | |
| `emoji` | char(16), blank | Shown on the Labels tab. Matching ignores it, as today |
| `name` | char(60) | The Gmail label name without the emoji. Unique per user by letters only (`labels.key`) |
| `sublabels` | char(80), blank | `never`, or a rule like `one per trip`. Blank means "when useful" |
| `include` | text, blank, ≤ 500 chars | What belongs under the label (the positive example) |
| `exclude` | text, blank, ≤ 300 chars | What doesn't, and where it goes instead (the negative example) |
| `position` | int | Display order |

At most 30 labels per user, which keeps the classifier prompt bounded.

`User.labels_md` from v1 is dropped: v1's migration `0003` is replaced, not stacked on, since it was never committed.

Why rows and not markdown: the editor has one field per part of a label, so storing the parts directly needs no markdown round trip, and a bad edit can't break the parse.

## Prefill from labels.md

`labels.md` keeps its format. The parser splits each section into the new fields:

| labels.md | Field |
| --------- | ----- |
| `## 🎓 University` | emoji `🎓`, name `University` (a leading emoji is split off) |
| `Sub-labels: one per course or thesis` | sublabels |
| Lines starting with `Not ` | exclude, with the `Not ` kept |
| Every other line | include |

Example, Authorisations:

- include: "Things you act on to get or grant access: sign-in codes, 2FA codes, password resets, account verification, invitations to join an account, team or project."
- exclude: "Not warnings about your account (Alerts)."

When it happens:

- At signup, the new user gets one row per `labels.md` section.
- When the Labels tab loads or a run starts for a user with no rows (accounts made before this change, including yours), the same seeding runs once.
- A "Reset to defaults" button replaces the user's rows with the `labels.md` ones, after a confirm.

A user who deletes every row gets seeded again on the next load or run. That's acceptable: a user with zero labels can't use the agent anyway.

## Classifier prompt

Each label becomes one prompt line, as today, with the exclude part kept separate:

```
- 🎓 University: Courses, exams, thesis, professors and TU Berlin admin. Not PhD applications (phd admissions). Sub-labels: one per course or thesis.
```

The line uses the Gmail label name (which may carry its own emoji), as `describe` does now. `agent.py` and `actions.py` don't change.

## Reading labels at run time

`RunContext.labels_md` from v1 becomes `RunContext.labels: dict | None`, already parsed: `{key(name): {"name", "sublabels", "include", "exclude"}}`.

- `runner.context(user)` builds it from the user's `Label` rows.
- The CLI leaves it `None`, and `labels.load()` parses `labels.md`.
- `labels.load()` stays cached per run, so users never share labels in the scheduler process.

The scheduler doesn't reload code. Restart `make dev` after deploying, or it keeps the old shared labels.

## Labels tab

A "Labels" tab in the top nav, next to Dashboard, opens `/labels/`. (The first v2 draft put the editor on the dashboard; changed to its own tab on review.)

One row per label. Columns: **Emoji · Name · Sub-labels · Belongs here · Doesn't belong**.

- Emoji, name and sub-labels are one-line inputs. Sub-labels has the placeholder "when useful", and its help text says `never` turns them off.
- Belongs here and Doesn't belong are two-line textareas that grow with their content.
- Each row has a remove checkbox. One empty row at the bottom adds a label.
- One "Save labels" button for the whole table. A Django model formset, so no JavaScript is needed apart from the textareas growing.
- Under 720px wide, each row becomes a stacked card with labelled fields.

Gmail check, at the top of the card, using one `gmail.list_labels()` call:

- A row whose name Gmail doesn't have gets a "Not in Gmail" badge.
- Gmail top-level labels without a row are listed: "In Gmail but not described: Work, Receipts", with an "Add them" button that appends empty rows for them.
- If Google isn't connected or the call fails, the check is skipped with a one-line note and the editor still works.

Validation on save: name required (for a non-empty row), no two rows with the same name by letters only, no `/` in names, field length limits, at most 30 labels. Errors show on the row.

The v1 `/labels/` page, its template and the Settings-card link are removed.

## Code layout

| Piece | Where | What |
| ----- | ----- | ---- |
| Model | `accounts/models.py`, migration `0003_label` (replaces v1's) | `Label` (also stores the `Cleanup:` hint for sub-label cleanup); drop `User.labels_md` |
| Seeding | `accounts/models.py` | `seed_labels(user)` from `labels.parse(labels.md)`; `user_labels(user)` seeds when empty, used by the Labels tab and runs; signup seeds too |
| Parsing | `labels.py` | `parse` returns emoji, include and exclude; `describe` joins them; `load` reads `ctx.labels` or `labels.md` |
| Run context | `run_context.py` | `labels: dict \| None` replaces `labels_md` |
| Runner | `accounts/runner.py` | `context(user)` builds `labels` from rows |
| Forms | `accounts/forms.py` | `LabelForm` and a model formset with the checks above; v1's `LabelsForm` removed |
| Views and URLs | `accounts/views.py`, `accounts/urls.py` | `/labels/` shows and saves the formset with the Gmail check; `POST /labels/add-missing/`; `POST /labels/reset/` |
| Templates | `labels.html` (rewritten), `base.html` | Labels tab page; Dashboard and Labels tabs in the nav |
| Styles | `app.css` | Table, row inputs, stacked layout on phones |
| Tests | `accounts/tests.py` | See acceptance criteria |

## Acceptance criteria

- [ ] A new user's Labels tab shows the 11 labels from `labels.md` with emoji, name, sub-label rule, include and exclude filled in.
- [ ] An existing user with no rows gets the same on their next Labels tab load or run.
- [ ] Editing a field and saving changes the next run's classifier prompt for that user only.
- [ ] Two users in the same scheduler process get their own labels.
- [ ] Duplicate names, names with `/`, and more than 30 labels are rejected with row errors; nothing is saved.
- [ ] The CLI still reads `labels.md` and its prompt lines are unchanged.
- [ ] The Labels tab works when Google isn't connected; only the Gmail check is missing.

## Open questions

1. Should the emoji also be used when the agent creates a Gmail label? Not today, since the agent never creates top-level labels. Leaving it display-only.
2. Prefill at signup or only on first load? The spec does both; signup alone would miss existing accounts.
