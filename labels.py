"""Descriptions and sub-label rules for each Gmail label, read from labels.md."""

from functools import cache
from pathlib import Path

LABELS_FILE = Path(__file__).parent / "labels.md"


def key(name: str) -> str:
    """A label name by letters only, so emoji, spaces and case don't matter."""
    return "".join(c for c in name.lower() if c.isalpha())


@cache
def load() -> dict[str, dict]:
    """{key(label): {"name", "sublabels", "description"}} for each "## <label>" section."""
    entries, current = {}, None
    for line in LABELS_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("## "):
            name = line[3:].strip()
            current = entries[key(name)] = {"name": name, "sublabels": "", "description": ""}
        elif current is None or not line:
            continue
        elif line.lower().startswith("sub-labels:"):
            current["sublabels"] = line.split(":", 1)[1].strip()
        else:
            current["description"] = f"{current['description']} {line}".strip()
    return entries


def allows_sublabel(label_name: str) -> bool:
    return load().get(key(label_name), {}).get("sublabels", "").lower() != "never"


def describe(label_name: str) -> str:
    """The label as one prompt line: name, description and sub-label rule."""
    entry = load().get(key(label_name))
    if not entry:
        return label_name
    return f"{label_name}: {entry['description']} Sub-labels: {entry['sublabels'] or 'when useful'}."


def check(label_names: list[str]) -> list[str]:
    """Warnings for Gmail top-level labels missing from labels.md, and the other way round."""
    gmail_keys = {key(name): name for name in label_names if "/" not in name}
    warnings = [f"{name} has no description in labels.md" for k, name in gmail_keys.items() if k not in load()]
    warnings += [f"labels.md describes {entry['name']}, which Gmail doesn't have"
                 for k, entry in load().items() if k not in gmail_keys]
    return warnings
