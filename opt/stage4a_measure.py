"""OPT.Stage4a 측정 — 시나리오 1개. 첫 해 → OPT.Dec22 재시도 → 남은 예산 동안 LNS (OPT.Dec28, §7.9).

--mode alns    : 학습 없는 ALNS (R0 ~ R4, 적응 가중치) — 비교 (나)
--mode random  : 무작위 LNS (R1 만, 가중치 갱신 없음) — 비교 (가)
벽시계 판정 예산 (D139 정시 멈춤 opt/deadline.py) 이 기본. --expand-budget 을 주면 확장 수 예산으로 같은 프로세스에서
정시 멈춤 없이 돈다 (결정성 시험 — 작업자 1 ↔ 2, OPT.Req6 · Req7).
출력: out/stage4a/<mode>[_w<n>][_e<budget>]/<stem>_curve.json · _output.json · _det.json · _first_output.json ·
_summary.json · _opt_log.json · _lns.json (규칙 통계 · 반복 기록 · k 변화)
"""
import argparse
import json
import time
from pathlib import Path

from . import _sk  # noqa: F401
from .deadline import run_with_deadline
from .judge_check import check
from .lns import LNS
from .priority import SET_BUDGET_SEC
from .stage2_measure import root_parallel, set_of
from .stage3b import Search3b
from .stage3b_measure import _ser1, plan_from, summarize
from pipe_routing.anytime import machine_info  # noqa: E402
from pipe_routing.layered import layered_router_a  # noqa: E402
from pipe_routing.pipeline import deterministic_view, required_pipe_ids, run  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402


def make_runner(path, sc, set_name, budget, mode, workers, expand_budget, log, lns_expand=None):
    seed = (sc.meta or {}).get("config", {}).get("seed")

    def run_search(on_best, on_progress=None):
        S = Search3b(sc, layered_router_a, 60.0, None if (expand_budget or lns_expand) else budget, log, rule="fixed",
                     backtrack=False, on_best=on_best, expand_budget=expand_budget)
        t = time.perf_counter()
        req, exc = required_pipe_ids(sc)
        root = (root_parallel(path, workers, S.res) if workers > 1 else
                {"routes": {pid: layered_router_a(sc, S.pipes[pid], 60.0, ()) for pid in sorted(req)},
                 "excluded": sorted(exc)})
        log["root_sec"] = time.perf_counter() - t
        out = S.anytime(root)
        log["first_retry_sec"] = time.perf_counter() - t
        log["first_retry_expanded"] = S.expanded
        first = out["first"]
        lns = None
        if out["best_score"] is not None:
            S.budget = None                       # LNS 는 벽시계로 스스로 멈추지 않는다 — 부모 (정시 멈춤) 가 끊는다
            S.exhausted = False
            L = LNS(S, out["best"], out["best_score"], set_name=set_name, seed=seed, mode=mode, workers=workers,
                    root_routes=root["routes"], on_best=on_best, on_progress=on_progress,
                    expand_budget=(S.expanded + lns_expand) if lns_expand else expand_budget, t0=S.t0)
            lns = L.run()
        log["total_expanded"] = S.expanded
        return {"log": log, "points": out["points"] + (lns["points"] if lns else []), "lns": lns,
                "first": ({"routes": {k: _ser1(v) for k, v in first[0].routes.items()}, "failed": dict(first[0].failed),
                           "order": sorted(first[0].routes)} if first else None),
                "expanded": S.expanded}
    return run_search


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--mode", choices=["alns", "random"], required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--budget-sec", type=float, default=None, help="시험용 — 세트 예산 대신")
    ap.add_argument("--expand-budget", type=int, default=None)
    ap.add_argument("--lns-expand", type=int, default=None,
                    help="결정성 시험 — 첫 해 + 재시도는 예산 없이, 그 뒤 LNS 에 확장 수 N 만큼")
    ap.add_argument("--tag", default=None, help="출력 폴더 이름 (기본 = mode)")
    ap.add_argument("-o", "--out", default="out/stage4a")
    a = ap.parse_args(argv)
    jc = check()
    if not all(v["ok"] for v in jc.values()):
        raise SystemExit(f"심판 불일치 — 실행하지 않는다: {jc}")
    stem = Path(a.scenario).stem
    sc = load(a.scenario)
    set_name = set_of(a.scenario)
    budget = a.budget_sec or SET_BUDGET_SEC[set_name]
    d = Path(a.out) / (a.tag or a.mode)
    d.mkdir(parents=True, exist_ok=True)
    log, keep = {}, {}
    runner = make_runner(a.scenario, sc, set_name, budget, a.mode, a.workers, a.expand_budget, log, a.lns_expand)
    if a.expand_budget or a.lns_expand:
        best = {}

        def on_best(st, score, pt):
            best["payload"] = {"routes": {k: _ser1(v) for k, v in st.routes.items()}, "failed": dict(st.failed),
                               "order": sorted(st.routes)}
            best.setdefault("points", []).append(pt)

        def planner(sc_, router, tl):
            keep["final"] = runner(on_best)
            keep["info"] = {"killed_at_deadline": False, "has_solution": "payload" in best,
                            "points": best.get("points", []), "progress": keep["final"]["lns"]}
            return plan_from(sc_, best.get("payload", {"routes": {}, "failed": {}, "order": []}))
    else:
        def planner(sc_, router, tl):
            plan, info = run_with_deadline(sc_, runner, budget, progress=True)
            keep["final"], keep["info"] = info["final"], info
            return plan
    t = time.perf_counter()
    out = run(sc, router=layered_router_a, planner=planner, solo_ref=False)
    wall = time.perf_counter() - t
    final, info = keep.get("final"), keep["info"]
    flog = (final or {}).get("log", {})
    lns = (final or {}).get("lns") or info.get("progress") or {}
    points = info.get("points") or (final or {}).get("points") or []
    first_out = (run(sc, router=layered_router_a, planner=lambda sc_, r_, tl: plan_from(sc_, final["first"]), solo_ref=False)
                 if final and final.get("first") else None)
    curve = {"scenario": stem, "n_pipes": len(sc.pipes), "required_pipes": len(required_pipe_ids(sc)[0]),
             "grid_variant": "g0", "seed": (sc.meta or {}).get("config", {}).get("seed"),
             "search_budget": layered_router_a.budget_for(sc),
             "stop": ("expand_budget" if (a.expand_budget or a.lns_expand) else ("deadline" if info.get("killed_at_deadline") else "done")),
             "max_sec": budget, "max_expanded": a.expand_budget, "acceptance": "D77",
             "verified": None, "rejected": None,
             "t0_sec": (flog.get("first") or {}).get("sec"), "e0": (flog.get("first") or {}).get("expanded"),
             "moves": lns.get("iterations"), "total_expanded": lns.get("expanded", (final or {}).get("expanded")),
             "total_sec": round(out["global"]["routing_time_sec"], 3), "concurrency": a.concurrency,
             "workers": a.workers, "lns_mode": a.mode, "machine": machine_info(), "points": points,
             "deadline": {k: v for k, v in info.items() if k not in ("final", "points", "progress")}}
    (d / f"{stem}_curve.json").write_text(json.dumps(curve, ensure_ascii=False, indent=1))
    (d / f"{stem}_output.json").write_text(json.dumps(out, ensure_ascii=False))
    (d / f"{stem}_det.json").write_text(json.dumps(deterministic_view(out), ensure_ascii=False))
    if first_out:
        (d / f"{stem}_first_output.json").write_text(json.dumps(first_out, ensure_ascii=False))
    (d / f"{stem}_opt_log.json").write_text(json.dumps(flog, ensure_ascii=False))
    (d / f"{stem}_lns.json").write_text(json.dumps(lns, ensure_ascii=False))
    summ = {"scenario": stem, "mode": a.mode, "workers": a.workers, "budget_sec": budget, "expand_budget": a.expand_budget, "lns_expand": a.lns_expand,
            "wall_sec": wall, "routing_sec": out["global"]["routing_time_sec"], "t0_sec": curve["t0_sec"],
            "first": summarize(first_out) if first_out else None, "best": summarize(out),
            "stop": curve["stop"], "n_points": len(points), "iterations": lns.get("iterations"),
            "revived": lns.get("revived"), "retry_timeout": flog.get("retry_timeout"),
            "first_retry_sec": flog.get("first_retry_sec"), "total_expanded": curve["total_expanded"],
            "late_solutions": info.get("late_solutions")}
    (d / f"{stem}_summary.json").write_text(json.dumps(summ, ensure_ascii=False))
    print(json.dumps(summ, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
