"""Optional profile integration via installed entry points, never a core dependency."""

from importlib.metadata import entry_points
from pathlib import Path


def provider():
    found = list(entry_points(group="schedule_cli.profiles"))
    if len(found) != 1:
        raise ValueError(
            "Install one schedule_cli.profiles adapter to use --profile; standalone calendars use --data-dir"
        )
    return found[0].load()


def resolve(name):
    path = Path(provider()("resolve", name))
    if not path.is_absolute():
        raise ValueError("Profile adapter returned a non-absolute data directory")
    return path
