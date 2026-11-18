"""Checks for run isolation (plan S04)."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import runctx  # noqa: E402


@pytest.fixture(autouse=True)
def keep_cwd():
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


def _inputs(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    (d / "a.csv").write_text("x\n1\n", encoding="utf-8")
    return d


def test_output_dir_is_required(tmp_path):
    with pytest.raises(SystemExit):
        runctx.start("run_x.py", argv=[])


def test_run_is_isolated_and_inputs_copied(tmp_path):
    ind = _inputs(tmp_path)
    ctx = runctx.start("run_x.py", inputs=["a.csv"],
                       argv=["--output-dir", str(tmp_path / "runs"), "--run-id", "r1",
                             "--input-dir", str(ind)])
    assert ctx.out == (tmp_path / "runs" / "r1").resolve()
    assert (ctx.out / "a.csv").read_text(encoding="utf-8") == "x\n1\n"
    assert Path(ctx.cfg["paths"]["output_dir_abs"]) == ctx.out
    assert Path(os.getcwd()) == ctx.out
    ctx.finish()
    meta = json.loads((ctx.out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["execution_state"] == "completed" and "a.csv" in meta["inputs"]


def test_existing_run_dir_is_never_reused(tmp_path):
    ind = _inputs(tmp_path)
    args = ["--output-dir", str(tmp_path / "runs"), "--run-id", "same", "--input-dir", str(ind)]
    runctx.start("run_x.py", inputs=["a.csv"], argv=args)
    with pytest.raises(SystemExit):
        runctx.start("run_x.py", inputs=["a.csv"], argv=args)


def test_missing_input_creates_nothing(tmp_path):
    with pytest.raises(SystemExit):
        runctx.start("run_x.py", inputs=["a.csv"],
                     argv=["--output-dir", str(tmp_path / "runs"), "--run-id", "r2",
                           "--input-dir", str(tmp_path / "nowhere")])
    assert not (tmp_path / "runs" / "r2").exists()


def test_legacy_and_output_dir_are_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        runctx.start("run_x.py", argv=["--legacy-output", "--output-dir", str(tmp_path)])


def test_bad_run_id_and_unknown_input_rejected(tmp_path):
    ind = _inputs(tmp_path)
    with pytest.raises(SystemExit):
        runctx.start("run_x.py", argv=["--output-dir", str(tmp_path), "--run-id", "a/b"])
    with pytest.raises(SystemExit):
        runctx.start("run_x.py", inputs=["a.csv"],
                     argv=["--output-dir", str(tmp_path), "--input-dir", str(ind),
                           "--input", "zzz.csv=foo"])


def test_generated_ids_are_unique():
    ids = {runctx.new_run_id("s") for _ in range(200)}
    assert len(ids) == 200
