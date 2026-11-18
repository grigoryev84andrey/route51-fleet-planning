"""Run context — explicit configuration, isolated output directory, run identity.

Every working entry point starts with `runctx.start(...)`. It parses

  --config PATH        service configuration (default: service_config.yaml next to
                       this file; relative paths inside it resolve against the
                       directory of *that* file)
  --output-dir DIR     parent directory for this run; the run writes only into
                       DIR/<run_id>, created exclusively (an existing directory is
                       never reused, so two runs cannot share or overwrite outputs)
  --run-id ID          identifier of the run (default: <script>_<UTC stamp>_<8 hex>)
  --input-dir DIR      directory holding the upstream files this script reads
  --input NAME=PATH    override one upstream file (repeatable)
  --protocol PATH      frozen analysis protocol (recorded; required where a script
                       says so)
  --legacy-output      write into the config's historical `output/` directory, as
                       the scripts did before; must be asked for explicitly

Upstream inputs are copied into the run directory before the script starts, so a
run keeps an immutable copy of what it read. `run_meta.json` records argv,
effective configuration, input hashes, interpreter and library versions, and the
end state. Nothing is written to the historical `output/` unless
`--legacy-output` is given.
"""
from __future__ import annotations

import argparse
import atexit
import copy
import hashlib
import json
import os
import platform
import secrets
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml

SERVICE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = SERVICE_ROOT / "service_config.yaml"
RUNCTX_VERSION = "runctx_v1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _versions() -> dict:
    out = {"python": sys.version.split()[0], "platform": platform.platform()}
    for mod in ("numpy", "pandas", "scipy", "pymoo", "yaml"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:                                    # noqa: BLE001
            out[mod] = None
    return out


def new_run_id(script: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{script}_{stamp}_{secrets.token_hex(4)}"


def load_config(config_path: Path) -> dict:
    """Read the YAML and resolve its relative paths against its own directory."""
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg["_root"] = str(config_path.parent.resolve())
    return cfg


@dataclass
class RunContext:
    script: str
    run_id: str
    cfg: dict
    out: Path
    args: argparse.Namespace
    inputs: dict = field(default_factory=dict)
    legacy: bool = False
    meta_path: Path | None = None
    _meta: dict = field(default_factory=dict)

    def input(self, name: str) -> Path:
        return self.inputs[name]

    def record(self, **kv) -> None:
        self._meta.setdefault("notes", {}).update(kv)
        self._write_meta()

    def _write_meta(self) -> None:
        if self.meta_path is not None:
            self.meta_path.write_text(json.dumps(self._meta, indent=2, ensure_ascii=False,
                                                 default=str), encoding="utf-8")

    def finish(self, state: str = "completed") -> None:
        if self._meta.get("execution_state") in ("completed", "failed"):
            return
        self._meta["execution_state"] = state
        self._meta["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        outputs = {}
        for p in sorted(self.out.rglob("*")):
            if p.is_file() and p.name != "run_meta.json" and "replications" not in p.parts:
                outputs[str(p.relative_to(self.out)).replace("\\", "/")] = _sha256(p)
        self._meta["outputs_sha256"] = outputs
        self._write_meta()


def start(script_file: str, *, inputs: Iterable[str] = (),
          optional_inputs: Iterable[str] = (),
          add_args: Callable[[argparse.ArgumentParser], None] | None = None,
          require_protocol: bool = False,
          argv: list[str] | None = None) -> RunContext:
    script = Path(script_file).stem
    ap = argparse.ArgumentParser(prog=script)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--input-dir", type=Path, default=None)
    ap.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--protocol", type=Path, default=None)
    ap.add_argument("--legacy-output", action="store_true")
    if add_args:
        add_args(ap)
    args = ap.parse_args(argv)

    if args.legacy_output and args.output_dir is not None:
        ap.error("--legacy-output and --output-dir are mutually exclusive")
    if not args.legacy_output and args.output_dir is None:
        ap.error("an explicit --output-dir is required (or --legacy-output to write into "
                 "the historical output/ directory)")
    if require_protocol and args.protocol is None:
        ap.error("--protocol is required for this script")
    if args.protocol is not None and not args.protocol.is_file():
        ap.error(f"protocol file not found: {args.protocol}")

    config_path = args.config.resolve()
    cfg = load_config(config_path)
    run_id = args.run_id or new_run_id(script)
    if any(c in run_id for c in '\\/:*?"<>|') or run_id in (".", ".."):
        ap.error(f"run id contains characters not allowed in a directory name: {run_id!r}")

    # --- upstream inputs: resolve all sources before anything is created -------
    overrides = {}
    for item in args.input:
        if "=" not in item:
            ap.error(f"--input expects NAME=PATH, got {item!r}")
        k, v = item.split("=", 1)
        overrides[k] = Path(v)
    wanted = list(inputs) + [n for n in optional_inputs if n not in inputs]
    unknown = set(overrides) - set(wanted)
    if unknown:
        ap.error(f"unknown --input names {sorted(unknown)}; this script reads {wanted}")
    legacy_out = (Path(cfg["_root"]) / cfg["paths"]["output_dir"]).resolve()
    sources: dict[str, Path] = {}
    for name in wanted:
        src = overrides.get(name)
        if src is None and args.input_dir is not None:
            src = args.input_dir / name
        if src is None and args.legacy_output:
            src = legacy_out / name
        if src is None or not src.is_file():
            if name in optional_inputs:
                continue
            ap.error(f"required input {name!r} not found "
                     f"(give --input-dir or --input {name}=PATH)")
        sources[name] = src.resolve()

    if args.legacy_output:
        out = legacy_out
        out.mkdir(parents=True, exist_ok=True)
    else:
        parent = args.output_dir.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        out = parent / run_id
        try:
            out.mkdir(exist_ok=False)                       # exclusive: no shared directory
        except FileExistsError:
            ap.error(f"run directory already exists: {out}")
    cfg["paths"]["output_dir_abs"] = str(out)

    # --- copy inputs into the run and hash them ----------------------------------
    resolved: dict[str, Path] = {}
    in_meta: dict[str, dict] = {}
    for name, src in sources.items():
        dst = out / name
        if not args.legacy_output and src != dst:
            shutil.copy2(src, dst)
        resolved[name] = dst
        in_meta[name] = {"source": str(src), "sha256": _sha256(dst)}

    protocol = None
    if args.protocol is not None:
        protocol = {"path": str(args.protocol.resolve()), "sha256": _sha256(args.protocol)}
        if not args.legacy_output:
            shutil.copy2(args.protocol, out / "protocol.json")

    effective = copy.deepcopy(cfg)
    meta = {
        "runctx_version": RUNCTX_VERSION,
        "run_id": run_id,
        "script": script,
        "script_sha256": (_sha256(Path(script_file).resolve())
                          if Path(script_file).is_file() else None),
        "argv": sys.argv if argv is None else [script_file, *argv],
        "cwd": os.getcwd(),
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "effective_config": effective,
        "legacy_output": bool(args.legacy_output),
        "inputs": in_meta,
        "protocol": protocol,
        "versions": _versions(),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "execution_state": "running",
    }
    ctx = RunContext(script=script, run_id=run_id, cfg=cfg, out=out, args=args,
                     inputs=resolved, legacy=bool(args.legacy_output),
                     meta_path=None if args.legacy_output else out / "run_meta.json",
                     _meta=meta)
    ctx._write_meta()

    # A script that raises leaves `failed`; one that returns normally is `completed`.
    def _on_exit():
        ctx.finish("completed")
    atexit.register(_on_exit)
    prev_hook = sys.excepthook

    def _hook(tp, val, tb):
        ctx.finish("failed")
        prev_hook(tp, val, tb)
    sys.excepthook = _hook

    if not args.legacy_output:
        # Tools such as SUMO's randomTrips.py write into the process working
        # directory; keep those files inside this run.
        os.chdir(out)
    return ctx
