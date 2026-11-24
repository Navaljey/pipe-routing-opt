"""OPT.Stage3 측정 — 시나리오 1개.

--mode opt       : 우선순위 탐색 + 충돌 3 + anytime (세트 판정 예산), 루트 작업자 2
--mode opt_first : 같은 탐색을 첫 해까지만 (충돌 고르기 규칙 비교용 — OPT.Dec23)
--mode baseline  : skeleton anytime 기준선 (`python -m pipe_routing.anytime`, 같은 예산)
--rule fixed|history (OPT.Dec23)
출력 (out/<mode>[_<rule>]/): <stem>_curve.json (skeleton anytime 형식) · _output.json · _first_output.json ·
_summary.json · _opt_log.json · learn/<stem>.jsonl (학습 기록, 식별자 없음)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import _sk  # noqa: F401
from .judge_check import check
from .priority import SET_BUDGET_SEC
from .stage2_measure import root_parallel, set_of
from .stage3 import make_planner3
from pipe_routing.anytime import machine_info  # noqa: E402
from pipe_routing.layered import layered_router_a  # noqa: E402
from pipe_routing.pipeline import deterministic_view, required_pipe_ids, run  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402


def summarize(out):
    g = out["global"]
    return {"routed": g["routed_count"], "passed": g["success_count"], "J_total": g["J_total"],
            "full_success": g["full_success"], "required": g["required_pipes"], "required_passed": g["required_passed"],
            "member_blockage": g["member_blockage"]["count"], "support_violation": g["member_blockage"]["support_violation"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--mode", choices=["opt", "opt_first", "baseline"], required=True)
    ap.add_argument("--rule", choices=["fixed", "history"], default="fixed")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("-o", "--out", default="out/stage3")
    a = ap.parse_args(argv)
    jc = check()
    if not all(v["ok"] for v in jc.values()):
        raise SystemExit(f"심판 불일치 — 실행하지 않는다: {jc}")
    stem = Path(a.scenario).stem
    budget = SET_BUDGET_SEC[set_of(a.scenario)]
    if a.mode == "baseline":
        d = Path(a.out) / "baseline"
        d.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(_sk.SKELETON))
        t = time.perf_counter()
        subprocess.run([sys.executable, "-m", "pipe_routing.anytime", str(Path(a.scenario).resolve()), "-o", str(d.resolve()),
                        "--max-sec", str(budget), "--concurrency", str(a.concurrency)], env=env, check=True,
                       cwd=str(_sk.SKELETON))
        out = json.loads((d / f"{stem}_output.json").read_text())
        first = json.loads((d / f"{stem}_first_output.json").read_text())
        curve = json.loads((d / f"{stem}_curve.json").read_text())
        summ = {"scenario": stem, "mode": "baseline", "budget_sec": budget, "wall_sec": time.perf_counter() - t,
                "t0_sec": curve["t0_sec"], "first": summarize(first), "best": summarize(out),
                "n_points": len(curve["points"])}
        (d / f"{stem}_summary.json").write_text(json.dumps(summ, ensure_ascii=False))
        print(json.dumps(summ, ensure_ascii=False), flush=True)
        return 0

    sc = load(a.scenario)
    log, keep, learn = {}, {}, []
    root_fn = (lambda sc_, router, tl, res: root_parallel(a.scenario, a.workers, res)) if a.workers > 1 else None
    planner = make_planner3(budget, rule=a.rule, root_fn=root_fn, log=log, learn=learn, keep=keep,
                            first_only=(a.mode == "opt_first"))
    t = time.perf_counter()
    out = run(sc, router=layered_router_a, planner=planner, solo_ref=False)
    wall = time.perf_counter() - t
    d = Path(a.out) / f"{a.mode}_{a.rule}"
    d.mkdir(parents=True, exist_ok=True)
    S = keep["search"]
    first_out = run(sc, router=layered_router_a, planner=lambda sc_, r_, tl: keep["first_plan"], solo_ref=False) \
        if keep.get("first_plan") else None
    curve = {"scenario": stem, "n_pipes": len(sc.pipes), "required_pipes": len(required_pipe_ids(sc)[0]),
             "grid_variant": "g0", "seed": 0, "search_budget": layered_router_a.budget_for(sc),
             "stop": log["stop"], "max_sec": budget, "max_expanded": None,
             "acceptance": "D77", "verified": log["n_verify"], "rejected": sum(1 for x in log["dives"] if x["score"]) - len(keep["points"]),
             "t0_sec": log["first"]["sec"] if log["first"] else None, "e0": log["first"]["expanded"] if log["first"] else None,
             "moves": len(log["dives"]), "total_expanded": S.expanded, "total_sec": round(out["global"]["routing_time_sec"], 3),
             "concurrency": a.concurrency, "workers": a.workers, "rule": a.rule, "machine": machine_info(),
             "points": keep["points"]}
    (d / f"{stem}_curve.json").write_text(json.dumps(curve, ensure_ascii=False, indent=1))
    (d / f"{stem}_output.json").write_text(json.dumps(out, ensure_ascii=False))
    (d / f"{stem}_det.json").write_text(json.dumps({k: v for k, v in deterministic_view(out).items()}, ensure_ascii=False))
    if first_out:
        (d / f"{stem}_first_output.json").write_text(json.dumps(first_out, ensure_ascii=False))
    (d / f"{stem}_opt_log.json").write_text(json.dumps(log, ensure_ascii=False))
    ld = d / "learn"
    ld.mkdir(exist_ok=True)
    with open(ld / f"{stem}.jsonl", "w") as f:
        for row in learn:
            f.write(json.dumps({k: v for k, v in row.items() if k != "_ids"} | {"scenario_seed": sc.meta.get("config", {}).get("seed")},
                               ensure_ascii=False) + "\n")
    summ = {"scenario": stem, "mode": a.mode, "rule": a.rule, "budget_sec": budget, "wall_sec": wall,
            "routing_sec": out["global"]["routing_time_sec"], "t0_sec": curve["t0_sec"],
            "first": summarize(first_out) if first_out else None, "best": summarize(out),
            "stop": log["stop"], "dives": len(log["dives"]), "n_points": len(keep["points"]),
            "root_sec": log["root_sec"], "n_reroutes": len(log["reroutes"]),
            "retry_timeout": log.get("retry_timeout"), "unresolved_child": len(log.get("unresolved_child", []))}
    (d / f"{stem}_summary.json").write_text(json.dumps(summ, ensure_ascii=False))
    print(json.dumps(summ, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
