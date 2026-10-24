# AI Agent and Skills: Outline

Oct 24, 2026 · @Ryan

## Overview

An AI agent is a model that runs in a loop: it reads a goal, decides on a next step, calls tools, observes the results, and repeats until the goal is met or it stops.

- A plain model call answers once. An agent acts over several steps.
- An agent needs three things: a goal, a set of tools, and a loop that decides what to do next.
- Skills are reusable, packaged know-how that the agent loads when a task needs it.

Principles:

- no additional interface for the customer (except agent settings)
- as invisible as possible

## Core components

Define each component before writing any skill. Fill in the right column for your agent.

| Component    | What to define                                       | Your agent                          |
| ------------ | ---------------------------------------------------- | ----------------------------------- |
| Goal         | One sentence: what the agent achieves and for whom   | emaila agent that is inivisble&#32; |
| Model        | Which model, and whether subagents use a cheaper one | Claude Opus 5.5 (`claude-opus-5-5`) for both steps |
| Instructions | System prompt: role, tone, hard rules, when to stop  | Two prompts in `agent.py`: triage (pick 0 to 3 labels with sub-labels, urgency, minutes needed, summary) and act (take extra actions, don't repeat done ones, stop when nothing more helps) |
| Tools        | APIs, MCP servers, file access, web search           | gmail & gcal MCP                    |
| Memory       | What persists between runs, and where it is stored   | topics&#32;                         |
| Loop         | Plan, act, observe, repeat; max steps per run        | Per email: classify (one call), run required actions in code, draft a reply if `needs_reply` (one call), then Claude calls tools for extras; max 5 steps |
| Subagents    | Which tasks get delegated, and what each returns     | Classifier today; digest writer planned (see Subagents) |
| Triggers     | Chat, schedule, webhook, or another agent            | incoming emails                     |
|              |                                                      |                                     |

## Tools

A tool is a function the agent can call: a name, a description, typed inputs, and a result it reads back. Tools are the only way the agent changes anything outside itself, so each one needs a clear owner and approval rule.

- Description: when to call it, in one or two sentences; the model reads only this
- Inputs: typed and minimal; reject bad input with an error message, don't crash
- Called by: code (always runs, as a required step), the agent (its own choice), or both

### Tools inventory

| Tool | What it does | Inputs | Returns | Called by | Needs approval |
| ---- | ------------ | ------ | ------- | --------- | -------------- |
| `apply_label` | Applies a Gmail label to the email (real Gmail), plus an optional sub-label under it (`Parent/Child`), created if new. The parent is applied too. Sub-labels are one level deep, and never under labels whose rule in `labels.md` is `never` (Authorisations, Alerts, Newsletters, Shopping). An email holds at most 3 of your labels, sub-labels included | message id, top-level label name, optional sub-label name | `labelled <name>`, `labelled <name> and <name>/<sub> (new sub-label)`, or an error | Code (every email) and agent | No |
| `notify_now` | Alerts the user right away (stub: prints) | one-sentence summary | `notified` | Code (`instant`) and agent | No |
| `add_to_digest` | Adds the email to the daily digest (stub: prints) | one-sentence summary | `added to digest` | Code (`for_summary`) and agent | No |
| `block_time` | Blocks calendar time to respond to the email (stub: prints). Should pick a slot before any deadline, inside working hours, not overlapping | title, minutes | the blocked slot | Code (`schedule_time`) and agent | No |
| `draft_reply` | Saves a reply as a Gmail draft in the email's thread; never sends. Reply all adds the other To/Cc recipients (not you), except for mailing lists or more than 10 recipients, and puts a check note at the top for you to delete. Skips if the thread already has a draft | message id, body, reply all, reason | `draft saved`, `draft saved, reply all`, `draft saved to sender only (...)`, or `skipped` | Code only (`needs_reply`, after `write-reply`); not an agent tool | No (draft only) |
| `find_free_slots` (planned) | Finds free calendar slots for a meeting | minutes, latest date | list of free slots | Agent | No |

## Skills

A skill is a folder of instructions the agent loads only when a task needs it. Tools give the agent abilities; skills tell it how to use them well for one kind of task.

- `SKILL.md`: name, one-line description (the trigger), and step-by-step instructions
- `scripts/`: code the agent runs instead of rewriting it each time
- `references/`: templates, examples, rules, loaded only when a step needs them

Make something a skill when the same multi-step procedure recurs. Keep one-off tasks in the prompt.

### Skills inventory

| Skill | Trigger (when to use) | Inputs | Output | Tools needed |
| ----- | --------------------- | ------ | ------ | ------------ |
| `write-reply` (`skills/write-reply/SKILL.md`) | Any time a reply gets drafted (code calls it when `needs_reply`) | email incl. To/Cc, classification, actions already done | reply body in your voice (tone, language, formality, sign-off) plus `reply_all` and its reason | `draft_reply` |
|       |                       |        |        |              |
|       |                       |        |        |              |

## Skill design

The description decides whether a skill fires, so write it before the instructions. Check each skill against this list:

- [ ] Scope: one job per skill; split it if the description needs "and"
- [ ] Name: short, kebab-case, says what it does (`cv-builder`, not `helper-2`)
- [ ] Description: when to use it, with the words a user would actually type; under 200 characters
- [ ] Negative triggers: when not to use it, if it overlaps with another skill
- [ ] Steps: numbered, each one checkable; ends with a verification step
- [ ] Inputs: what it needs, and what to ask for if missing
- [ ] Output: exact format and where it goes
- [ ] Deterministic parts moved into `scripts/`
- [ ] Long reference material moved into `references/`

## Agent-skill interaction

The agent sees only skill names and descriptions until one matches; the full instructions load on demand, which keeps the context small.

1. Request arrives (chat, schedule, or another agent)
2. Agent compares the request against all skill descriptions
3. Match found: load that `SKILL.md`; no match: handle with base instructions
4. Follow the skill's steps, calling tools and scripts as it says
5. Load files from `references/` only when a step asks for them
6. Run the skill's verification step
7. Return the output, or hand off to the next skill or subagent

## Subagents

A subagent is a separate model call with its own instructions, tools and context. The main agent hands it one task and gets back only the result, which keeps the main context small.

- Use one when a task is self-contained, or would fill the main context with reading
- Give it only the tools its task needs
- It can run on a cheaper model than the main agent
- Define exactly what it returns, so the main agent can use it without rereading

### Subagents inventory

| Subagent | Task delegated | Inputs | Returns | Model | Tools |
| -------- | -------------- | ------ | ------- | ----- | ----- |
| classifier | Triage one email (already a separate call in `agent.py`) | email, top-level labels (read from Gmail per email) with their description and sub-label rule from `labels.md` and their existing sub-labels | 0 to 3 labels (none if nothing fits), each with whether a sub-label is needed and its name (at most 3 labels in total, sub-labels included), urgency, minutes needed, summary | Opus 5.5 today; try Haiku 5.5 after measuring | none |
| digest writer (planned) | Summarise the day's digest items, once a day | that day's `add_to_digest` items | one short summary | a cheaper model, e.g. Haiku 5.5 | none |
|          |                |        |         |       |       |

## Guardrails and evaluation

Decide what the agent may do alone and what needs your approval, then test each skill against real requests.

| Area        | Decide                                                               | Your agent |
| ----------- | -------------------------------------------------------------------- | ---------- |
| Permissions | Read-only vs. write; which actions need approval (send, delete, pay) | Gmail scope `gmail.modify`: read, label and create sub-labels (one level deep, never under labels marked `never` in `labels.md`; at most 3 labels per email, sub-labels included). Replies are only ever drafts, never sent; reply-all drafts are blocked for mailing lists and more than 10 recipients, and carry a check note. No deleting. Nothing needs approval yet |
| Limits      | Max steps, tokens, or cost per run                                   | Newest 5 emails per run by default; max 5 tool steps per email; email body cut at 8,000 characters |
| Failure     | Retry once, then stop and report                                     | An error on one email is printed and the run moves on to the next; no retry yet |
| Data        | What the agent must never store or send                              | `credentials.json` and `token.json` are git-ignored. Email content is sent to the Anthropic API; the agent stores nothing itself |
| Logging     | What each run records for later review                               | Prints the classification and every action to the terminal; nothing is saved |

**Evaluating skills**

- Trigger tests: 5–10 requests that should fire the skill, and 5 that should not
- Output tests: run each test request, check the output against the skill's format
- Regression: rerun the test set after every change to a description or step

## Next steps

- [ ] Write the agent's goal in one sentence
- [ ] Fill in the core components table
- [ ] List 3–5 skills in the inventory, starting with the most frequent task
- [ ] Draft the description for the first skill and run trigger tests
- [ ] Set the permissions and limits before the first unattended run

**Open questions**

- Single agent with skills, or an orchestrator with subagents?
- Where does memory live: files, a database, or a notes app?

- can we integrate dynamic emails ?
