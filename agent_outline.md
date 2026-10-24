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
| Model        | Which model, and whether subagents use a cheaper one | claude                              |
| Instructions | System prompt: role, tone, hard rules, when to stop  | TODO                                |
| Tools        | APIs, MCP servers, file access, web search           | gmail & gcal MCP                    |
| Memory       | What persists between runs, and where it is stored   | topics&#32;                         |
| Loop         | Plan, act, observe, repeat; max steps per run        | TODO                                |
| Subagents    | Which tasks get delegated, and what each returns     | ??                                  |
| Triggers     | Chat, schedule, webhook, or another agent            | incoming emails                     |
|              |                                                      |                                     |

## Skills

A skill is a folder of instructions the agent loads only when a task needs it. Tools give the agent abilities; skills tell it how to use them well for one kind of task.

- `SKILL.md`: name, one-line description (the trigger), and step-by-step instructions
- `scripts/`: code the agent runs instead of rewriting it each time
- `references/`: templates, examples, rules, loaded only when a step needs them

Make something a skill when the same multi-step procedure recurs. Keep one-off tasks in the prompt.

### Skills inventory

| Skill | Trigger (when to use) | Inputs | Output | Tools needed |
| ----- | --------------------- | ------ | ------ | ------------ |
|       |                       |        |        |              |
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

## Guardrails and evaluation

Decide what the agent may do alone and what needs your approval, then test each skill against real requests.

| Area        | Decide                                                               | Your agent |
| ----------- | -------------------------------------------------------------------- | ---------- |
| Permissions | Read-only vs. write; which actions need approval (send, delete, pay) |            |
| Limits      | Max steps, tokens, or cost per run                                   |            |
| Failure     | Retry once, then stop and report                                     |            |
| Data        | What the agent must never store or send                              |            |
| Logging     | What each run records for later review                               |            |

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
