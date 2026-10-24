"""Minimal email agent: classifies the newest inbox emails by label and urgency,
runs the required actions for that urgency, then lets Claude take more.

Usage:
    python agent.py [N]    # process the newest N inbox emails (default 5)
"""

import sys
from email.utils import parseaddr
from typing import Literal

import anthropic
from pydantic import BaseModel, create_model

import actions
import fake_claude
import gmail

MODEL = "claude-opus-5-5"
DEFAULT_COUNT = 5

# True: print each Claude request and use canned answers from fake_claude.py
# (only works for its test ids msg_001..msg_004, not real Gmail ids).
FAKE_CLAUDE = False

Urgency = Literal["not_urgent", "for_summary", "schedule_time", "instant"]

SYSTEM_PROMPT = """You triage incoming emails.
Pick exactly one label from the allowed labels.
Pick the urgency:
- not_urgent: no attention needed
- for_summary: worth knowing, can wait for a digest
- schedule_time: urgent, needs a prompt reply that takes some time to prepare
- instant: needs attention right now
Estimate minutes_needed: how many minutes the user needs to respond to the email,
for example to find a meeting time and reply. Count only the response, not any
work the email asks for.
Give a one-sentence summary."""


class Email(BaseModel):
    id: str
    sender: str
    to: str
    subject: str
    body: str


def result_model(label_names: list[str]) -> type[BaseModel]:
    # Built per run so the label enum always matches what Gmail returns.
    return create_model(
        "Result",
        label=(Literal[tuple(label_names)], ...),
        urgency=(Urgency, ...),
        minutes_needed=(int, ...),
        summary=(str, ...),
    )


def classify(email: Email, label_names: list[str]) -> BaseModel:
    request = {
        "model": MODEL,
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Allowed labels: {', '.join(label_names)}\n\nEmail:\n"
            + email.model_dump_json(indent=2),
        }],
    }
    if FAKE_CLAUDE:
        return fake_claude.classify(request, email.id, result_model(label_names))

    client = anthropic.Anthropic()
    response = client.messages.parse(**request, output_format=result_model(label_names))
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model refused: {response.stop_details}")
    return response.parsed_output


def sender_name(email: Email) -> str:
    name, address = parseaddr(email.sender)
    return name or address


# The minimum that always happens, run by code (not Claude).
# Each entry: (action, how to build its arguments from the email and result).
ALWAYS = [(actions.apply_label, lambda email, r: {"message_id": email.id, "label_name": r.label})]

REQUIRED = {
    "not_urgent": [],
    "for_summary": [(actions.add_to_digest, lambda email, r: {"summary": r.summary})],
    "schedule_time": [(actions.block_time, lambda email, r: {
        "message_id": email.id,
        "title": f"Reply to {sender_name(email)}: {email.subject}",
        "minutes": r.minutes_needed,
        "notes": f"From: {email.sender}\nSubject: {email.subject}\n"
        f"Label: {r.label}\n\n{r.summary}",
    })],
    "instant": [(actions.notify_now, lambda email, r: {"summary": r.summary})],
}

MAX_STEPS = 5

ACT_PROMPT = """You handle an email that has already been classified, and its required actions are already done.
Use the tools to take any further actions that would genuinely help the user.
Don't repeat an action that has already been done. If nothing more is needed, take no action.
Finish with one line saying what you did."""


def run_required(email: Email, result) -> list[str]:
    done = []
    for action, build_args in ALWAYS + REQUIRED[result.urgency]:
        args = build_args(email, result)
        done.append(f"{action.__name__}({args}) -> {action(**args)}")
    return done


def act(email: Email, result, done: list[str]) -> None:
    request = {
        "model": MODEL,
        "max_tokens": 16000,
        "system": ACT_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Email:\n{email.model_dump_json(indent=2)}\n\n"
            f"Classification:\n{result.model_dump_json(indent=2)}\n\n"
            "Already done:\n" + ("\n".join(done) or "nothing"),
        }],
    }
    if FAKE_CLAUDE:
        print("[agent]", fake_claude.act(request, email.id))
        return

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
    if not label_names:
        sys.exit("No user labels in Gmail. Create the labels the agent should choose from.")

    for message in gmail.list_newest(count):
        email = Email(**message)
        print(f"\n=== {email.subject} ({email.sender})")
        try:
            run(email, label_names)
        except Exception as e:
            print(f"[error] {email.id}: {e}")


if __name__ == "__main__":
    main()
