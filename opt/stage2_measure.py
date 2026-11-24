"""OPT.Stage2 측정 — 시나리오 1개를 OPT (우선순위 탐색, 첫 해) 또는 기준선 (순차 + rip-up 첫 해) 으로 돌린다.

둘 다 skeleton pipeline.run (같은 검증기 · J) 으로 채점한다. 기준선 첫 해 = sequential_ripup_planner(classify=False)
— E1 의 "첫 해 = anytime 첫 점" 과 같이 실패 분류 (진단 재라우팅) 는 시간에 넣지 않는다 (D110 ②).
사용: python -m opt.stage2_measure <scenario.json> --mode opt|baseline -o out/stage2
"""
import argparse
import json
import time
from functools import partial
from pathlib import Path

from . import _sk  # noqa: F401
from .judge_check import check
from .priority import SET_BUDGET_SEC, make_planner
from .root import build_root
from pipe_routing.multi import sequential_ripup_planner  # noqa: E402
from pipe_routing.pipeline import deterministic_view, run  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402


def root_parallel(path: str, workers: int, res) -> dict:
    """작업자 루트 (opt.root.build_root) → planner 의 루트 형식. 결과는 순차 루트와 바이트 동일 (OPT.Stage1 40/40)."""
    from pipe_routing.router_astar import RouteResult
    root = build_root(path, workers)
    routes = {}
    for x in root["pipes"]:
        if x["status"] == "excluded":
            continue
        routes[x["pipe_id"]] = RouteResult(x["pipe_id"], x["status"], J=x["J_router"], J_support_est=x["J_support_est"],
                                           J_penetration=x["J_penetration"], waypoints=x["waypoints"],
                                           expanded=x["expanded"], search_sec=x["search_sec"])
        res.attempts.setdefault(x["pipe_id"], []).append({"status": x["status"], "graph_sec": round(x["graph_sec"], 3),
                                                          "search_sec": round(x["search_sec"], 3), "n_obstacle_pipes": 0})
    return {"routes": routes, "excluded": root["excluded"], "cpu_sec_workers": root["cpu_sec_workers"]}


def set_of(path: str) -> str:
    n = Path(path).stem
    for k in ("dense30", "dense50", "stair", "pair"):
        if k in n:
            return k
    return "base"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--mode", choices=["opt", "baseline"], required=True)
    ap.add_argument("--workers", type=int, default=2, help="OPT 루트 작업자 수 (OPT.Dec16: 2 = 판정 조건, 1 = 보조)")
    ap.add_argument("-o", "--out", default="out/stage2")
    a = ap.parse_args(argv)
    jc = check()
    if not all(v["ok"] for v in jc.values()):
        raise SystemExit(f"심판 불일치 — 실행하지 않는다: {jc}")
    sc = load(a.scenario)
    log = {}
    if a.mode == "opt":
        root_fn = (lambda sc_, router, tl, res: root_parallel(a.scenario, a.workers, res)) if a.workers > 1 else None
        planner = make_planner(budget_sec=SET_BUDGET_SEC[set_of(a.scenario)], root_fn=root_fn, log=log)
    else:
        planner = partial(sequential_ripup_planner, classify=False)
    t = time.perf_counter()
    out = run(sc, planner=planner, solo_ref=False)
    wall = time.perf_counter() - t
    g = out["global"]
    summ = {"scenario": Path(a.scenario).stem, "mode": a.mode, "routing_sec": g["routing_time_sec"], "wall_sec": wall,
            "routed": g["routed_count"], "passed": g["success_count"], "J_total": g["J_total"],
            "full_success": g["full_success"], "required": g["required_pipes"], "required_passed": g["required_passed"]}
    if a.mode == "opt":
        summ.update(workers=a.workers, root_sec=log["root_sec"], search_sec=log["search_sec"],
                    first_solution=log["first_solution"], budget_exhausted=log["budget_exhausted"],
                    n_reroutes=len(log["reroutes"]))
    d = Path(a.out) / (a.mode if a.mode == "baseline" else f"opt_w{a.workers}")
    d.mkdir(parents=True, exist_ok=True)
    stem = Path(a.scenario).stem
    (d / f"{stem}_summary.json").write_text(json.dumps(summ, ensure_ascii=False))
    det = deterministic_view(out)
    det["global"].pop("fixed_layers", None)   # 이 프로세스가 만든 고정층 목록 — 작업자 배치에 따라 다른 보고 필드 (판정 결과 아님)
    (d / f"{stem}_det.json").write_text(json.dumps(det, ensure_ascii=False))
    if log:
        (d / f"{stem}_opt_log.json").write_text(json.dumps(log, ensure_ascii=False))
    print(json.dumps(summ, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
