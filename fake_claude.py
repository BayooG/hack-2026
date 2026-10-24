"""Stand-in for the Claude API. Prints the request and returns a canned answer.

Canned answers are keyed by email id; add one here for each new test email.
"""

import actions

# What the classify call would return for each test email.
CLASSIFICATIONS = {
    "msg_001": {"label": "Newsletters", "urgency": "not_urgent", "minutes_needed": 10,
                "summary": "ML Weekly newsletter with new interpretability papers."},
    "msg_002": {"label": "University", "urgency": "for_summary", "minutes_needed": 1,
                "summary": "The main library opens at 9:00 on weekdays from 1 December."},
    "msg_003": {"label": "PhD", "urgency": "schedule_time", "minutes_needed": 15,
                "summary": "Prof. Weber wants an hour next week to discuss the chapter 3 draft."},
    "msg_004": {"label": "Medical", "urgency": "instant", "minutes_needed": 2,
                "summary": "Today's doctor's appointment moved to 15:00; confirm before 12:00."},
}

# The extra tool calls the act step would make for each test email, then its last line.
EXTRA_ACTIONS = {
    "msg_001": ([], "Nothing more needed."),
    "msg_002": ([], "Nothing more needed."),
    "msg_003": ([], "Nothing more needed; the calendar block covers it."),
    "msg_004": (
        [("draft_reply", {"to": "appointments@praxis-mueller.example.de",
                          "body": "Hello,\n\nI confirm the new appointment at 15:00 today.\n\nRyan Gumsheimer"})],
        "Drafted a confirmation for the 15:00 appointment.",
    ),
}


def _print_request(name: str, request: dict) -> None:
    print(f"[claude:{name}] would send:")
    for message in request["messages"]:
        print(message["content"])


def classify(request: dict, email_id: str, result_model):
    _print_request("classify", request)
    return result_model(**CLASSIFICATIONS[email_id])


def act(request: dict, email_id: str) -> str:
    _print_request("act", request)
    calls, final_text = EXTRA_ACTIONS[email_id]
    for name, args in calls:
        getattr(actions, name)(**args)
    return final_text
