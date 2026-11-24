"""OPT.Stage3 측정 (2026-11-25 점검 반영판) — 시나리오 1개.

--mode opt     : OPT.Dec28 (첫 해 + OPT.Dec22 재시도까지, 되돌아가기 끔) · D139 정시 멈춤 (세트 예산)
--mode opt_bt  : 같은 탐색 + 되돌아가기 켬 (측정 비교용 스위치) · D139 정시 멈춤
--mode record  : 조율 기록 실행 — 시드 31M 이고 OPT_COORD_RECORD_DIR (레포 밖) 가 있을 때만 기록. 확장 수 예산
                 (--expand-budget), 정시 멈춤 없음. 그 밖의 시드에서는 기록을 끄고 로그에 한 줄 남긴다.
출력: out/stage3b/<mode>/<stem>_curve.json · _output.json · _first_output.json(있으면) · _summary.json · _opt_log.json
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from . import _sk  # noqa: F401
from .coord_record import Recorder, record_enabled
from .deadline import run_with_deadline
from .judge_check import check, SKELETON_COMMIT, SUPPORT_SHA256
from .priority import SET_BUDGET_SEC
from .stage2_measure import root_parallel, set_of
from .stage3b import Search3b
from pipe_routing.anytime import machine_info  # noqa: E402
from pipe_routing.constants import PIPE_SPECS  # noqa: E402
from pipe_routing.layered import layered_router_a  # noqa: E402
from pipe_routing.multi import PlanResult  # noqa: E402
from pipe_routing.pipeline import deterministic_view, required_pipe_ids, run  # noqa: E402
from pipe_routing.router_astar import RouteResult  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402


def summarize(out):
    g = out["global"]
    return {"routed": g["routed_count"], "passed": g["success_count"], "J_total": g["J_total"],
            "full_success": g["full_success"], "required": g["required_pipes"], "required_passed": g["required_passed"],
            "member_blockage": g["member_blockage"]["count"], "support_violation": g["member_blockage"]["support_violation"]}


def opt_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(_sk.ROOT), text=True).strip()
    except Exception:  # noqa: BLE001
        return None


def make_search_runner(path, sc, budget, backtrack, workers, recorder=None, expand_budget=None, log=None):
    log = log if log is not None else {}

    def run_search(on_best):
        S = Search3b(sc, layered_router_a, 60.0, None if expand_budget else budget, log, rule="fixed",
                     backtrack=backtrack, recorder=recorder, on_best=on_best, expand_budget=expand_budget)
        t = time.perf_counter()
        root = (root_parallel(path, workers, S.res) if workers > 1 else
                {"routes": {pid: layered_router_a(sc, S.pipes[pid], 60.0, ()) for pid in sorted(required_pipe_ids(sc)[0])},
                 "excluded": sorted(required_pipe_ids(sc)[1])})
        log["root_sec"] = time.perf_counter() - t
        if recorder is not None:
            recorder.entities(root["routes"], lambda p: PIPE_SPECS[p.nominal_size])
        out = S.anytime(root)
        log["reroutes"] = S.reroutes
        log["total_expanded"] = S.expanded
        first = out["first"]
        return {"log": log, "points": out["points"],
                "first": ({"routes": {k: _ser1(v) for k, v in first[0].routes.items()}, "failed": dict(first[0].failed),
                           "order": sorted(first[0].routes)} if first else None),
                "best_failed": dict(out["best"].failed), "expanded": S.expanded,
                "records": (recorder.rows if recorder is not None else None)}
    return run_search


def _ser1(r):
    import dataclasses
    d = dataclasses.asdict(r)
    d.pop("states", None)
    return d


def plan_from(sc, payload):
    routes = {pid: RouteResult(**d) for pid, d in payload["routes"].items()}
    for p in sc.pipes:
        if p.id not in routes:
            why = payload["failed"].get(p.id, "")
            routes[p.id] = RouteResult(p.id, "timeout" if why == "solo:timeout" else "unreachable")
    order = payload["order"] + sorted(payload["failed"])
    return PlanResult(routes=routes, order=order, planner="opt_priority_search", placement=list(order))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--mode", choices=["opt", "opt_bt", "record"], required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--budget-sec", type=float, default=None, help="시험용 — 세트 예산 대신")
    ap.add_argument("--expand-budget", type=int, default=None)
    ap.add_argument("-o", "--out", default="out/stage3b")
    a = ap.parse_args(argv)
    jc = check()
    if not all(v["ok"] for v in jc.values()):
        raise SystemExit(f"심판 불일치 — 실행하지 않는다: {jc}")
    stem = Path(a.scenario).stem
    sc = load(a.scenario)
    budget = a.budget_sec or SET_BUDGET_SEC[set_of(a.scenario)]
    seed = (sc.meta or {}).get("config", {}).get("seed")
    d = Path(a.out) / a.mode
    d.mkdir(parents=True, exist_ok=True)
    log = {}
    recorder = None
    if a.mode == "record":
        on, why = record_enabled(seed)
        log["record"] = why
        print(f"{stem}: {why}", flush=True)
        if on:
            recorder = Recorder(sc, stem, {"opt_commit": opt_commit(), "skeleton_commit": SKELETON_COMMIT,
                                           "support_sha256": SUPPORT_SHA256, "props_source": None,
                                           "set": set_of(a.scenario), "seed": seed, "template": None,
                                           "budget": {"unit": "expansions", "limit": a.expand_budget},
                                           "workers": a.workers, "machine": machine_info(),
                                           "map_mm": list(sc.block.extent), "grid_cell_mm": None})
    keep = {}
    if a.mode == "record":
        # 기록 실행: 확장 수 예산, 같은 프로세스, 정시 멈춤 없음. 최선 해는 on_best 로 받아 둔다.
        best = {}

        def on_best(st, score, pt):
            best["payload"] = {"routes": {k: _ser1(v) for k, v in st.routes.items()}, "failed": dict(st.failed),
                               "order": sorted(st.routes)}
        runner = make_search_runner(a.scenario, sc, budget, False, a.workers, recorder, a.expand_budget, log)

        def planner(sc_, router, tl):
            keep["final"] = runner(on_best)
            keep["info"] = {"killed_at_deadline": False, "has_solution": "payload" in best}
            return plan_from(sc_, best.get("payload", {"routes": {}, "failed": {}, "order": []}))
    else:
        runner = make_search_runner(a.scenario, sc, budget, a.mode == "opt_bt", a.workers, None, None, log)

        def planner(sc_, router, tl):
            plan, info = run_with_deadline(sc_, runner, budget)
            keep["final"], keep["info"] = info["final"], info
            return plan

    t = time.perf_counter()
    out = run(sc, router=layered_router_a, planner=planner, solo_ref=False)
    wall = time.perf_counter() - t
    final, info = keep.get("final"), keep["info"]
    flog = (final or {}).get("log", {})
    points = (final or {}).get("points") or info.get("points") or []
    first_out = (run(sc, router=layered_router_a, planner=lambda sc_, r_, tl: plan_from(sc_, final["first"]), solo_ref=False)
                 if final and final.get("first") else None)
    curve = {"scenario": stem, "n_pipes": len(sc.pipes), "required_pipes": len(required_pipe_ids(sc)[0]),
             "grid_variant": "g0", "seed": 0, "search_budget": layered_router_a.budget_for(sc),
             "stop": flog.get("stop", "deadline" if info.get("killed_at_deadline") else None),
             "max_sec": budget, "max_expanded": a.expand_budget, "acceptance": "D77",
             "verified": flog.get("n_verify"), "rejected": None,
             "t0_sec": (flog.get("first") or {}).get("sec"), "e0": (flog.get("first") or {}).get("expanded"),
             "moves": len(flog.get("dives", [])), "total_expanded": (final or {}).get("expanded"),
             "total_sec": round(out["global"]["routing_time_sec"], 3), "concurrency": a.concurrency,
             "workers": a.workers, "backtrack": a.mode == "opt_bt", "deadline": info, "machine": machine_info(),
             "points": points}
    curve["deadline"] = {k: v for k, v in info.items() if k not in ("final", "points")}
    (d / f"{stem}_curve.json").write_text(json.dumps(curve, ensure_ascii=False, indent=1))
    (d / f"{stem}_output.json").write_text(json.dumps(out, ensure_ascii=False))
    (d / f"{stem}_det.json").write_text(json.dumps(deterministic_view(out), ensure_ascii=False))
    if first_out:
        (d / f"{stem}_first_output.json").write_text(json.dumps(first_out, ensure_ascii=False))
    (d / f"{stem}_opt_log.json").write_text(json.dumps(flog, ensure_ascii=False))
    if recorder is not None and final:
        recorder.rows = final["records"]
        import os
        recorder.write(os.environ["OPT_COORD_RECORD_DIR"])
    summ = {"scenario": stem, "mode": a.mode, "budget_sec": budget, "wall_sec": wall,
            "routing_sec": out["global"]["routing_time_sec"], "t0_sec": curve["t0_sec"],
            "first": summarize(first_out) if first_out else None, "best": summarize(out),
            "stop": curve["stop"], "deadline": curve["deadline"], "n_points": len(points),
            "retry_timeout": flog.get("retry_timeout"), "unresolved_child": len(flog.get("unresolved_child", [])),
            "record": log.get("record")}
    (d / f"{stem}_summary.json").write_text(json.dumps(summ, ensure_ascii=False))
    print(json.dumps(summ, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
