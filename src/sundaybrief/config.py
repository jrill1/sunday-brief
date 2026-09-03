"""Load and validate sources.yaml into plain dicts the pipeline can iterate.

A source URL may be given literally, or as `secret: SOME_ENV_NAME` so that the
actual (bearer-capability) iCal URL lives in your .env / Keychain and never in
the repo.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .secrets import get_secret

VALID_TYPES = {
    "ical", "rss", "wp-events", "libnet-events", "mec-events",
    "cityspark-events", "worldwebs-events", "headless",
}
VALID_CATEGORIES = {"work", "personal", "daycare", "local"}


class ConfigError(Exception):
    pass


def load_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config not found at {path}. Copy config/sources.example.yaml to "
            f"config/sources.yaml and fill it in."
        )
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}

    sources = cfg.get("sources", [])
    resolved = []
    for i, s in enumerate(sources):
        if s.get("enabled") is False:
            continue
        stype = s.get("type")
        if stype not in VALID_TYPES:
            raise ConfigError(f"sources[{i}]: type must be one of {sorted(VALID_TYPES)}, got {stype!r}")
        cat = s.get("category")
        if cat not in VALID_CATEGORIES:
            raise ConfigError(f"sources[{i}]: category must be one of {sorted(VALID_CATEGORIES)}, got {cat!r}")

        url = s.get("url")
        if not url and s.get("secret"):
            url = get_secret(s["secret"], required=True)
        if not url:
            raise ConfigError(f"sources[{i}] ({s.get('name')!r}): needs a 'url' or a 'secret' key")

        resolved.append({
            "name": s.get("name", f"source-{i}"),
            "type": stype,
            "category": cat,
            "url": url,
            "person": s.get("person", ""),
            "child": s.get("child", ""),
        })

    summary = cfg.get("summary", {})
    summary.setdefault("style", "narrative")     # templated | narrative
    summary.setdefault("model", "claude-sonnet-5")
    summary.setdefault("full_brief_url", "")
    summary.setdefault("names", {})              # {"me": "...", "spouse": "..."}, narrative-only
    summary.setdefault("children", [])           # [{"name","age_months","as_of"}], see summarize._child_ages_phrase

    return {"sources": resolved, "summary": summary, "window_days": cfg.get("window_days", 7)}
