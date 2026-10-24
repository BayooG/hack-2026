"""Descriptions and sub-label rules for each Gmail label.

Web runs use the labels the user edits on the Labels tab; the CLI reads labels.md,
which is also where new users' labels come from.
"""

from pathlib import Path

import run_context

LABELS_FILE = Path(__file__).parent / "labels.md"


def key(name: str) -> str:
    """A label name by letters only, so emoji, spaces and case don't matter."""
    return "".join(c for c in name.lower() if c.isalpha())


def split_emoji(name: str) -> tuple[str, str]:
    """("✈️", "Travel") for "✈️ Travel": everything before the first letter or digit is the emoji."""
    for i, c in enumerate(name):
        if c.isalnum():
            return name[:i].strip(), name[i:].strip()
    return "", name.strip()


def parse(text: str) -> dict[str, dict]:
    """{key(label): {"name", "emoji", "sublabels", "cleanup", "include", "exclude"}} for each
    "## <label>" section. Lines from the first one starting with "Not " are the exclude part."""
    entries, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            emoji, name = split_emoji(line[3:])
            current = entries[key(name)] = {
                "name": name, "emoji": emoji, "sublabels": "", "cleanup": "", "include": "", "exclude": "",
            }
        elif current is None or not line:
            continue
        elif line.lower().startswith("sub-labels:"):
            current["sublabels"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("cleanup:"):
            # Its own field, so cleanup hints never reach the classifier prompt.
            current["cleanup"] = line.split(":", 1)[1].strip()
        else:
            part = "exclude" if current["exclude"] or line.lower().startswith("not ") else "include"
            current[part] = f"{current[part]} {line}".strip()
    return entries


def load() -> dict[str, dict]:
    """The current run's labels: the user's own on the web, labels.md in the CLI."""
    ctx = run_context.current()
    if ctx.labels is not None:
        return ctx.labels
    return ctx.cached("labels", lambda: parse(LABELS_FILE.read_text()))


def allows_sublabel(label_name: str) -> bool:
    return load().get(key(label_name), {}).get("sublabels", "").lower() != "never"


def is_described(label_name: str) -> bool:
    return key(label_name) in load()


def cleanup_hint(label_name: str) -> str:
    """When the label's sub-labels can be cleaned up, as written on its Cleanup: line."""
    return load().get(key(label_name), {}).get("cleanup", "")


def cleanup_never(label_name: str) -> bool:
    return cleanup_hint(label_name).lower() == "never"


def describe(label_name: str) -> str:
    """The label as one prompt line: name, what belongs and doesn't, and sub-label rule."""
    entry = load().get(key(label_name))
    if not entry:
        return label_name
    return f"{label_name}: {description(entry)} Sub-labels: {entry['sublabels'] or 'when useful'}."


def description(entry: dict) -> str:
    """What belongs under the label, then what doesn't."""
    return " ".join(part for part in (entry["include"], entry["exclude"]) if part)


def check(label_names: list[str]) -> list[str]:
    """Warnings for Gmail top-level labels without a description, and the other way round."""
    gmail_keys = {key(name): name for name in label_names if "/" not in name}
    warnings = [f"{name} has no description" for k, name in gmail_keys.items() if k not in load()]
    warnings += [f"{entry['name']} is described, but Gmail doesn't have it"
                 for k, entry in load().items() if k not in gmail_keys]
    return warnings

