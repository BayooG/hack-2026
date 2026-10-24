# Sub-label Cleanup: Spec

Oct 24, 2026 · @Ryan · Status: draft for review

## Overview

The classifier creates a sub-label for each trip, event, application or course. Over time most of them go quiet and clutter Gmail's sidebar and the classifier's "Existing sub-labels" list. Sub-label cleanup deletes the ones that are no longer useful.

- Separate from the email agent: its own module, its own log, and its own triggers: a "Clean up sub-labels" button on the dashboard, and a scheduled run once a month. A cleanup failure never stops email processing, and the other way round.
- Code finds candidates and enforces the safety rules. A model makes the judgment call on each candidate.
- Only sub-labels are ever deleted. Top-level labels are never touched.

## Goals and non-goals

Goals:

- Delete sub-labels that are done (trip over, application closed, one-off email).
- Keep sub-labels that look quiet but aren't done (trip next month, exam in February).
- Explain every decision in one line, and keep a log of them.

Non-goals (first version):

- Merging duplicate sub-labels (`Lisbon` and `Lisbon Trip`).
- Moving emails between labels.
- Touching top-level labels, sub-labels deeper than one level, or sub-labels under labels not in `labels.md`.
- Any change to the email agent (`agent.py`, `actions.py`, `accounts/runner.py`).

## Safety rules (enforced in code)

1. Only labels named `Parent/Child` with exactly one `/` are considered. Top-level labels and deeper labels are skipped.
2. The parent must have a section in `labels.md`.
3. Parents whose `Cleanup:` line is `never` are skipped entirely and never sent to the model.
4. The delete function refuses any label name without exactly one `/`, and logs an error. This is a second guard in case a bug passes a top-level label through.
5. The delete uses the label id code found in step 1, never a name written by the model.
6. Right before deleting, code re-reads the sub-label's newest email. If it changed since the model was asked, the delete is skipped and the sub-label is reviewed again next run.
7. Grace period of 14 days (`GRACE_DAYS`):
   - A sub-label whose newest email is less than 14 days old is never sent to the model.
   - A sub-label is never deleted less than 14 days after the thing it's about ended (the trip, event, exam or application decision). The model reports that end date as `ended_on`. If it is less than 14 days ago, code turns the delete into a keep with `recheck_after` set to `ended_on` + 14 days, whatever the model said.

Deleting a sub-label doesn't delete any email. `apply_label` always applies the parent too, so the emails stay under the top-level label.

## Triggers

- **Button:** "Clean up sub-labels" on the dashboard, next to "Run now", and disabled the same way when Google isn't connected. It works like "Run now": the click sets `cleanup_requested_at`, and the scheduler picks it up within about 10 seconds. Next to the button the dashboard shows only how many sub-labels the last run removed ("Last cleanup Oct 24: 3 sub-labels removed"). In a dry run: "would remove 3".
- **Monthly:** the scheduler runs cleanup for each connected user whose `last_cleanup_at` is empty or more than 30 days ago. A button click also counts as that month's run, because it sets `last_cleanup_at` too.
- Both triggers run the same code. While `cleanup_enabled` is off, both are dry runs.

## Flow

Each run, for one user:

1. **List** the user's Gmail labels and keep those passing safety rules 1 to 3.
2. **Skip** sub-labels with a `SublabelReview` row whose `recheck_after` is in the future.
3. **Measure** each remaining sub-label: conversation count (`threadsTotal` from `labels.get`) and the date of the newest and oldest email.
4. **Pick candidates.** A sub-label is a candidate when either holds:
   - at most 1 conversation, and no new email in 14 days;
   - no new email in 45 days.

   Both rules already respect the 14-day grace period on the newest email. Sub-labels that are not candidates are left alone and are not sent to the model.
5. **Judge** each candidate with one model call (see Model call).
6. **Act:** `delete` removes the Gmail label (after rules 6 and 7); `keep` stores `recheck_after`.
7. **Log** a `SublabelReview` row for every candidate, kept or deleted.

## Model call

One call per candidate, with structured output like the classifier's.

Model: `claude-sonnet-5-5`, the same as the classifier. Volume is a handful of candidates per user per month.

Input:

- Today's date.
- The parent label: name, description and `Cleanup:` hint from `labels.md`.
- The sub-label name, conversation count, first and last email dates.
- The last 5 emails under the sub-label: date, sender, subject, Gmail snippet. No full bodies.

Output:

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `decision` | `keep` or `delete` | |
| `reason` | string | One sentence, shown in the log |
| `ended_on` | date or null | When the thing the sub-label is about ended or will end (trip, event, exam, decision). Null when there is no such date, e.g. a one-off email |
| `recheck_after` | date or null | For `keep`: when to look again, at least 14 days after `ended_on`. Null means the default |

Prompt rules:

- Delete when the thing the sub-label is about ended at least 14 days ago, or when it is a one-off that will not get another email.
- Keep when it ended less than 14 days ago, and set `recheck_after` to 14 days after the end.
- Keep when something is still ahead: a future trip, event, exam, interview or decision mentioned in the emails.
- When unsure, keep.
- Follow the parent's `Cleanup:` hint.

If `recheck_after` is null or more than 180 days away, code uses today + 30 days. Because runs are monthly, a sub-label is looked at again on the first run on or after its `recheck_after`, so it may wait up to a month longer.

## `Cleanup:` lines in labels.md

A new optional line in each `labels.md` section, next to `Sub-labels:`. It follows the same convention: `never` is enforced in code, anything else is a hint for the model.

| Label | `Cleanup:` |
| ----- | ---------- |
| University | after the course's exam period; never the thesis |
| Job Applications | once rejected, withdrawn, or silent for two months |
| Travel | after the trip is over |
| Events | after the event date |
| Medical | never |
| phd admissions | once a decision has come in or the cycle has closed |

Labels with `Sub-labels: never` need no `Cleanup:` line. A label without one gets the default judgment.

`labels.py` must parse the new line into its own field. Today any unknown line is appended to the description, which would put cleanup hints into the classifier prompt.

## Data

New model `SublabelReview` in `accounts`:

| Field | Type | Notes |
| ----- | ---- | ----- |
| `user` | FK User | |
| `label_id` | string | Gmail label id |
| `label_name` | string | `Parent/Child`, kept after deletion for the log |
| `threads` | int | Conversation count when reviewed |
| `last_email_at` | datetime | |
| `decision` | `keep`, `delete`, `would_delete`, `error` | `would_delete` in dry run |
| `reason` | text | From the model, or the error |
| `recheck_after` | date, null | |
| `reviewed_at` | datetime | |

New fields:

- `GoogleConnection.last_cleanup_at` (datetime, null): set at the end of every run, button or monthly.
- `User.cleanup_requested_at` (datetime, null): set by the button, cleared by the scheduler before the run, like `run_requested_at`.
- `User.cleanup_enabled` (bool, default false): a switch in the dashboard's agent settings. While it is off, cleanup runs as a dry run and logs `would_delete`.

## Code layout

| Piece | Where | What |
| ----- | ----- | ---- |
| Cleanup logic | `sublabel_cleanup.py` (new, repo root like `gcal.py`) | List, measure, pick candidates, call the model, delete. Has a CLI: `python sublabel_cleanup.py [--apply]`, dry run by default, for the token.json account |
| Gmail helpers | `gmail.py` | `get_label(id)` (counts), `delete_label(id)`, newest/oldest message dates and the last N subjects and snippets under a label |
| Parsing | `labels.py` | Read the `Cleanup:` line; `cleanup_hint(label)`, `cleanup_never(label)` |
| Web run | `accounts/cleanup_runner.py` (new) | Runs cleanup for one user in their `RunContext`, writes `SublabelReview` rows |
| Button | `accounts/views.py`, `urls.py`, `dashboard.html` | `cleanup_now` view (POST, sets `cleanup_requested_at`), the button, the last-run line, and the `cleanup_enabled` switch in agent settings |
| Trigger | `run_scheduler` | On a button request, or when `last_cleanup_at` is over 30 days old. A separate step from `runner.run_user`, with its own error handling |
| Make target | `Makefile` | `make cleanup` (dry run), `make cleanup APPLY=1` |

The Gmail scope `gmail.modify` already allows deleting labels, so users don't need to reconnect.

## Examples

| Sub-label | Facts | Expected |
| --------- | ----- | -------- |
| `Travel/Lisbon` | 4 conversations, last 50 days ago, trip Nov 20 to 25 | keep, recheck Dec 9 (trip end + 14 days) |
| `Travel/Paris` | 3 conversations, last 50 days ago, trip ended Oct 19 | model says delete, code keeps until Nov 2 (grace period) |
| `Travel/Rome` | 6 conversations, last 60 days ago, trip was in August | delete |
| `Events/Berlin Hackathon` | 2 conversations, event was last week | not a candidate yet (last email 7 days ago) |
| `Job Applications/Acme ML Engineer` | 1 conversation, 20 days ago, "we'll get back to you in January" | keep, recheck in January |
| `Job Applications/Foo Data Scientist` | 3 conversations, rejection 50 days ago | delete |
| `Medical/Dr Weber` | parent is `Cleanup: never` | never reviewed |
| `University` | top-level | never considered |

## Acceptance criteria

- [ ] No code path can delete a label without exactly one `/`; a test passes a top-level name to the delete function and checks it refuses.
- [ ] Sub-labels under `Cleanup: never` parents and under parents missing from `labels.md` are never sent to the model.
- [ ] Non-candidates are never sent to the model.
- [ ] A kept sub-label isn't reviewed again before its `recheck_after`.
- [ ] With `cleanup_enabled` off, nothing is deleted and rows say `would_delete`.
- [ ] A new email arriving between the model call and the delete cancels the delete.
- [ ] `Cleanup:` lines don't show up in the classifier's label descriptions.
- [ ] An exception in cleanup doesn't affect `runner.run_user`, and the other way round.
- [ ] Clicking the button runs cleanup within one scheduler poll, and sets `last_cleanup_at`.
- [ ] The scheduler runs cleanup for a user only when `last_cleanup_at` is empty or over 30 days old, or the button was clicked.
- [ ] The button is disabled when Google isn't connected.
- [ ] The dashboard shows only the number of sub-labels the last run removed; the per-sub-label log is in the admin page.
- [ ] A sub-label whose newest email is under 14 days old is never sent to the model.
- [ ] A delete with `ended_on` less than 14 days ago is turned into a keep with `recheck_after` = `ended_on` + 14 days; a test checks this with the model's answer mocked.

## Open questions

1. Thresholds: 14 days for single-conversation sub-labels and 45 days for quiet ones. Keep both global, or allow a number in `Cleanup:` later?
2. Monthly means "30 days after the last run". Is that fine, or should it be a fixed day, like the 1st of each month?
