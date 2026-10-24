"""Minimal email agent: classifies the newest inbox emails by label and urgency,
runs the required actions for that urgency, drafts a reply when one is needed,
then lets Claude take more.

Usage:
    python agent.py [N]    # process the newest N inbox emails (default 5)
"""

import sys
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, create_model

import actions
import gmail
import labels

MODEL = "claude-sonnet-5-5"
DEFAULT_COUNT = 5
SKILLS_DIR = Path(__file__).parent / "skills"

Urgency = Literal["not_urgent", "for_summary", "schedule_time", "instant"]

SYSTEM_PROMPT = """You triage incoming emails.
Pick 0 to 3 labels from the allowed labels, best fit first. Each label comes with a
description of what belongs under it and what doesn't; follow it. Usually one is right;
add another only when the email clearly belongs under it too. If no label fits, give
an empty list rather than forcing one.
Pick the urgency:
- not_urgent: no attention needed
- for_summary: worth knowing, can wait for a digest
- schedule_time: urgent, needs a prompt reply that takes some time to prepare
- instant: needs attention right now
Estimate minutes_needed: how many minutes the user needs to respond to the email,
for example to find a meeting time and reply. Count only the response, not any
work the email asks for.
Set needs_reply to true only if a person is waiting for a written answer from the user;
false for newsletters, notifications, receipts and no-reply senders.
Give a one-sentence summary.
For each label, decide whether the email needs a sub-label under it, following the label's
sub-label rule. Set sublabel_needed to true only if the label covers several separate things
that each get more than one email, such as an event, a project, a trip or an application;
then the sub-label groups the emails about that one thing. Never for streams like newsletters,
receipts or notifications, and never for labels whose sub-label rule is "never".
If one of the label's existing sub-labels fits, use its exact name; otherwise give a short
new name of 1 to 4 words without "/". Set sublabel to null when sublabel_needed is false.
Each sub-label counts as a label: at most 3 in total, for example one label with a
sub-label plus one more label."""


class Email(BaseModel):
    id: str
    thread_id: str
    sender: str
    to: str
    cc: str
    subject: str
    body: str


def result_model(label_names: list[str]) -> type[BaseModel]:
    # Built per run so the label enum always matches what Gmail returns.
    label_choice = create_model(
        "LabelChoice",
        label=(Literal[tuple(label_names)], ...),
        sublabel_needed=(bool, ...),
        sublabel=(str | None, ...),
    )
    return create_model(
        "Result",
        labels=(list[label_choice], ...),
        urgency=(Urgency, ...),
        minutes_needed=(int, ...),
        needs_reply=(bool, ...),
        summary=(str, ...),
    )


def label_menu(label_names: list[str]) -> tuple[list[str], str]:
    """The top-level labels, and a prompt listing each with its description from
    labels.md and its existing sub-labels."""
    top = [name for name in label_names if "/" not in name]
    lines = []
    for parent in top:
        line = f"- {labels.describe(parent)}"
        if labels.allows_sublabel(parent):
            # Only one level deep, so deeper labels made by hand are left out.
            subs = [name.split("/", 1)[1] for name in label_names
                    if name.startswith(parent + "/") and name.count("/") == 1]
            if subs:
                line += f" Existing sub-labels: {', '.join(subs)}."
        lines.append(line)
    return top, "\n".join(lines)


def classify(email: Email, label_names: list[str]) -> BaseModel:
    top, menu = label_menu(label_names)
    request = {
        "model": MODEL,
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Allowed labels:\n{menu}\n\nEmail:\n"
                + email.model_dump_json(indent=2),
            }
        ],
    }
    client = anthropic.Anthropic()
    response = client.messages.parse(**request, output_format=result_model(top))
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model refused: {response.stop_details}")
    return response.parsed_output


def pick_labels(result) -> list[tuple[str, str | None]]:
    """The classifier's (label, sub-label) pairs, without repeats and cut to
    actions.MAX_LABELS in total, each sub-label counting as one."""
    picked, room = [], actions.MAX_LABELS
    for choice in result.labels:
        if any(label == choice.label for label, _ in picked):
            continue
        sublabel = choice.sublabel if choice.sublabel_needed and labels.allows_sublabel(choice.label) else None
        if room < 1:
            print(f"[labels] dropped {choice.label}: max {actions.MAX_LABELS} labels")
            continue
        if sublabel and room < 2:
            print(f"[labels] dropped sub-label {sublabel}: max {actions.MAX_LABELS} labels")
            sublabel = None
        picked.append((choice.label, sublabel))
        room -= 2 if sublabel else 1
    return picked


# The minimum that always happens, run by code (not Claude): labelling, then the
# actions for the urgency. Each entry: (action, how to build its arguments from the email and result).
REQUIRED = {
    "not_urgent": [],
    "for_summary": [(actions.add_to_digest, lambda email, r: {"summary": r.summary})],
    "schedule_time": [
        (
            actions.block_time,
            lambda email, r: {
                "title": f"Respond: {email.subject}",
                "minutes": r.minutes_needed,
            },
        )
    ],
    "instant": [(actions.notify_now, lambda email, r: {"summary": r.summary})],
}

MAX_STEPS = 5

def load_skill(name: str) -> str:
    """The instructions of skills/<name>/SKILL.md, without its frontmatter."""
    text = (SKILLS_DIR / name / "SKILL.md").read_text()
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


class Reply(BaseModel):
    body: str
    reply_all: bool
    reply_all_reason: str


ACT_PROMPT = """You handle an email that has already been classified, and its required actions are already done.
Use the tools to take any further actions that would genuinely help the user.
Don't repeat an action that has already been done. If nothing more is needed, take no action.
Finish with one line saying what you did."""


def run_required(email: Email, result) -> list[str]:
    done = []
    for label, sublabel in pick_labels(result):
        args = {"message_id": email.id, "label_name": label, "sublabel": sublabel}
        done.append(f"apply_label({args}) -> {actions.apply_label(**args)}")
    for action, build_args in REQUIRED[result.urgency]:
        args = build_args(email, result)
        done.append(f"{action.__name__}({args}) -> {action(**args)}")
    if result.needs_reply:
        done.append(auto_draft(email, result, done))
    return done


def write_reply(email: Email, result, done: list[str]) -> Reply:
    request = {
        "model": MODEL,
        "max_tokens": 4096,
        "system": load_skill("write-reply"),
        "messages": [
            {
                "role": "user",
                "content": f"Email:\n{email.model_dump_json(indent=2)}\n\n"
                f"Classification:\n{result.model_dump_json(indent=2)}\n\n"
                "Already done:\n" + ("\n".join(done) or "nothing"),
            }
        ],
    }
    client = anthropic.Anthropic()
    response = client.messages.parse(**request, output_format=Reply)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model refused: {response.stop_details}")
    return response.parsed_output


def auto_draft(email: Email, result, done: list[str]) -> str:
    # Checked before calling Claude so reruns don't pay for a draft that gets skipped.
    if gmail.thread_has_draft(email.thread_id):
        return "draft_reply -> skipped: this thread already has a draft"
    reply = write_reply(email, result, done)
    outcome = actions.draft_reply(email.id, reply.body, reply.reply_all, reply.reply_all_reason)
    return f"draft_reply(message_id={email.id!r}, reply_all={reply.reply_all}) -> {outcome}"


def act(email: Email, result, done: list[str]) -> None:
    request = {
        "model": MODEL,
        "max_tokens": 16000,
        "system": ACT_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Email:\n{email.model_dump_json(indent=2)}\n\n"
                f"Classification:\n{result.model_dump_json(indent=2)}\n\n"
                "Already done:\n" + ("\n".join(done) or "nothing"),
            }
        ],
    }
    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        **request, tools=actions.TOOLS, max_iterations=MAX_STEPS
    )
    final = None
    for message in runner:
        final = message
    if final.stop_reason == "refusal":
        raise RuntimeError(f"Model refused: {final.stop_details}")
    print("[agent]", "".join(b.text for b in final.content if b.type == "text"))


def run(email: Email, label_names: list[str]) -> None:
    result = classify(email, label_names)
    print(result.model_dump_json(indent=2))

    done = run_required(email, result)
    act(email, result, done)


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COUNT
    label_names = [label["name"] for label in gmail.list_labels()]
    if not [name for name in label_names if "/" not in name]:
        sys.exit(
            "No user labels in Gmail. Create the labels the agent should choose from."
        )
    for warning in labels.check(label_names):
        print(f"[labels] {warning}")

    for message in gmail.list_newest(count):
        email = Email(**message)
        print(f"\n=== {email.subject} ({email.sender})")
        try:
            # Read per email, so a sub-label made for one email is reused by the next.
            label_names = [label["name"] for label in gmail.list_labels()]
            run(email, label_names)
        except Exception as e:
            print(f"[error] {email.id}: {e}")


if __name__ == "__main__":
    main()
