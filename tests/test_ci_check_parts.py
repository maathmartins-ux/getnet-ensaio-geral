"""A CI roda o check em 3 jobs paralelos (core, replay, docker): em fila ele levava 15–20 min nos ensaios 13 e 14 e o
resultado do último commit chegava depois do T+59. As 3 partes juntas têm de ser o check inteiro, sem etapa perdida
nem repetida, e só o check inteiro reescreve o reports/verify.md."""
import sys
from pathlib import Path

import yaml

from scripts import check

ROOT = Path(__file__).resolve().parents[1]
PARTS = ("core", "replay", "docker")


def _run(monkeypatch, part: str) -> tuple[list[str], bool]:
    names: list[str] = []
    wrote = []

    def fake(name):
        def _f(*_a, **_k):
            names.append(name)
            return (name, True, 0.0, "")
        return _f

    monkeypatch.setattr(check, "step", lambda name, *_a, **_k: fake(name)())
    for fn in ("cited_tests_pass", "committed_eval_is_current", "commit_titles_clean", "no_example_data",
               "officials_have_facts", "replay_gate"):
        monkeypatch.setattr(check, fn, fake(fn))
    monkeypatch.setattr(check, "delivery_doc_checks", lambda: [fake("delivery_doc_checks")()])
    monkeypatch.setattr(check, "docker_steps", lambda *_a, **_k: [fake("docker_steps")()])
    monkeypatch.setattr(check, "image_extras", lambda *_a: "hybrid")
    monkeypatch.setattr(check, "domain_started", lambda: True)
    monkeypatch.setattr(check, "write_verify_report", lambda *_a: wrote.append(True))
    monkeypatch.setattr(sys, "argv", ["check.py", "--docker", "--scale", "--part", part])
    assert check.main() == 0
    return names, bool(wrote)


def test_the_three_parts_are_the_whole_check_without_overlap(monkeypatch):
    whole, wrote_all = _run(monkeypatch, "all")
    parts = {p: _run(monkeypatch, p)[0] for p in PARTS}
    joined = [n for p in PARTS for n in parts[p]]
    assert sorted(joined) == sorted(whole), "etapa perdida ou repetida entre as partes"
    assert len(set(joined)) == len(joined)
    assert parts["replay"] == ["replay_gate"]
    assert "docker_steps" in parts["docker"] and "réplicas (conversa, caso único, queda)" in parts["docker"]
    assert "pytest" in parts["core"] and "eval gate" in parts["core"]
    assert wrote_all


def test_only_the_whole_check_rewrites_verify_report(monkeypatch):
    for p in PARTS:
        assert _run(monkeypatch, p)[1] is False, p


def test_ci_runs_the_three_parts_in_parallel_jobs():
    job = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]["check"]
    assert job["strategy"]["matrix"]["part"] == list(PARTS)
    assert job["strategy"]["fail-fast"] is False  # um vermelho não cancela as outras partes
    runs = [s.get("run", "") for s in job["steps"]]
    assert "python scripts/check.py --docker --scale --part ${{ matrix.part }}" in runs
    leaks = next(s for s in job["steps"] if "gitleaks" in s.get("uses", ""))
    assert leaks.get("if") == "matrix.part == 'core'"  # o histórico inteiro varrido uma vez só


def test_early_signal_run_is_not_cancelled_by_pushes():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))  # o PyYAML lê a chave `on` como True
    assert "workflow_dispatch" in on
    assert "github.event_name" in wf["concurrency"]["group"]  # disparo à mão fora do grupo dos pushes
    assert wf["concurrency"]["cancel-in-progress"] is True  # pushes seguidos continuam se cancelando
