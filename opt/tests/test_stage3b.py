"""OPT.Stage3 점검 반영 시험 (느림 — 실제 라우팅). 비공개 서포트 표 (PIPE_SUPPORT_STANDARD) 가 있어야 한다.

① D139 정시 멈춤: 예산 20 s 로, 루트만 약 45 s 걸리는 시나리오를 돌리면 20 s 안에 돌아오고 해 없음 (모든 배관 경로 없음).
② 기록 문지기: 검증용 시드 (40M) 에서는 record 모드여도 기록하지 않는다.
③ 기록 켬 / 끔 같은 경로: 시드 31M 시나리오에서 기록 실행과 일반 실행 (OPT.Dec28) 의 판정 결과가 바이트 동일,
   기록 파일이 생기고 feat 묶음에 식별자가 없다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VAL = ROOT / "out" / "val" / "base" / "val_proc_000.json"
needs = pytest.mark.skipif(not os.environ.get("PIPE_SUPPORT_STANDARD") or not VAL.exists(),
                           reason="비공개 서포트 표 · 검증용 시나리오 필요")


def run(args, env=None, tmp=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([sys.executable, "-m", "opt.stage3b_measure", *args, "-o", str(tmp)], cwd=ROOT, env=e,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


@needs
def test_deadline_on_time(tmp_path):
    run([str(VAL), "--mode", "opt", "--workers", "1", "--budget-sec", "20"], tmp=tmp_path)
    s = json.loads((tmp_path / "opt" / "val_proc_000_summary.json").read_text())
    assert s["routing_sec"] <= 20.0, s
    assert s["deadline"]["killed_at_deadline"] is True
    assert s["deadline"]["has_solution"] is False
    assert s["best"]["routed"] == 0
    left = subprocess.run(["pgrep", "-f", "opt.stage3b_measure.*--budget-sec 20"], capture_output=True, text=True).stdout
    assert left.strip() == ""


@needs
def test_record_gate_off_on_validation_seed(tmp_path):
    out = run([str(VAL), "--mode", "record", "--workers", "1", "--expand-budget", "1"],
              env={"OPT_COORD_RECORD_DIR": str(tmp_path / "rec")}, tmp=tmp_path)
    assert "기록 범위" in out
    assert not (tmp_path / "rec").exists()


@needs
def test_record_same_path_and_no_ids(tmp_path):
    sys.path.insert(0, str(ROOT))
    from opt import _sk  # noqa: F401
    from pipe_routing.generator import GeneratorConfig, generate
    from pipe_routing.scenario import save
    sc = generate(GeneratorConfig(seed=31_000_000, n_pipes=10), "rec_proc_000")
    p = tmp_path / "rec_proc_000.json"
    save(sc, p)
    rec = tmp_path / "rec"
    run([str(p), "--mode", "record", "--workers", "1", "--expand-budget", str(10 ** 12)],
        env={"OPT_COORD_RECORD_DIR": str(rec)}, tmp=tmp_path)
    run([str(p), "--mode", "opt", "--workers", "1", "--budget-sec", "3600"], tmp=tmp_path)
    a = json.loads((tmp_path / "record" / "rec_proc_000_det.json").read_text())
    b = json.loads((tmp_path / "opt" / "rec_proc_000_det.json").read_text())
    for x in (a, b):
        x["global"].pop("fixed_layers", None)
    assert [r["waypoints"] for r in a["routes"]] == [r["waypoints"] for r in b["routes"]]
    assert a["global"]["J_total"] == b["global"]["J_total"]
    lines = [json.loads(x) for x in (rec / "rec_proc_000.coord.jsonl").read_text().splitlines()]
    assert lines[0]["kind"] == "header" and lines[0]["meta"]["schema"] == "coord_record/1"
    assert lines[0]["meta"]["epsilon"] == 0.0
    names = {e["id"]["name"] for e in lines if e["kind"] == "entity"}
    feats = json.dumps([e.get("feat") for e in lines[1:]])
    assert not any(f'"{n}"' in feats for n in names)
