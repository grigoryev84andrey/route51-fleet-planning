"""Configuration loader for the Route-51 synthetic multi-feed generator.

Loads config.yaml, resolves the data/output directories, and emits a load-time
warning listing every PLACEHOLDER value still in use (inherited hard-constraint
from the v2 spec: the pipeline runs on placeholders but must never hide them).
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import yaml


# Comment markers in config.yaml that flag a not-yet-calibrated value.
_PLACEHOLDER_TOKEN = "PLACEHOLDER"


class Config(dict):
    """Dict-with-attribute-access wrapper around the parsed YAML."""

    def __getattr__(self, item: str) -> Any:
        try:
            val = self[item]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(item) from exc
        return Config(val) if isinstance(val, dict) else val


def _scan_placeholders(yaml_text: str) -> list[str]:
    """Return the config keys whose line carries a PLACEHOLDER comment."""
    import re

    flagged: list[str] = []
    for raw in yaml_text.splitlines():
        if _PLACEHOLDER_TOKEN not in raw:
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            # Whole-line comment (a section caveat), not a key — record verbatim.
            flagged.append("(note) " + raw.strip().lstrip("# "))
        elif line.startswith("- "):
            # Flow-style list item, e.g. "- {id: A_X, ...}" — pull the id.
            m = re.search(r"id:\s*([A-Za-z0-9_]+)", line)
            flagged.append(m.group(1) if m else line)
        else:
            key = line.split(":", 1)[0].strip()
            if key:
                flagged.append(key)
    return flagged


def load_config(path: str | Path = "config.yaml", *, warn: bool = True) -> Config:
    """Load and lightly validate the YAML config.

    Parameters
    ----------
    path : path to config.yaml
    warn : if True, emit a UserWarning summarising outstanding placeholders.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    cfg = Config(yaml.safe_load(text))

    # Resolve relative data/output dirs against the config file's parent.
    root = path.parent
    cfg["run"]["_root"] = str(root)
    cfg["run"]["data_dir"] = str(root / cfg["run"]["data_dir"])
    cfg["run"]["output_dir"] = str(root / cfg["run"]["output_dir"])
    Path(cfg["run"]["output_dir"]).mkdir(parents=True, exist_ok=True)

    placeholders = _scan_placeholders(text)
    cfg["_placeholders"] = placeholders
    if warn and placeholders:
        keys = [p for p in placeholders if not p.startswith("(note)")]
        warnings.warn(
            f"{len(keys)} PLACEHOLDER values still in use "
            f"(calibration pending). First few: {keys[:8]}",
            UserWarning,
            stacklevel=2,
        )
    return cfg


if __name__ == "__main__":
    c = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    ph = [p for p in c["_placeholders"] if not p.startswith("(note)")]
    print(f"Loaded config. Seed={c.run.random_seed}. Placeholders flagged: {len(ph)}")
    for p in ph:
        print("  PLACEHOLDER:", p)
