"""D139 정시 멈춤 — 판정은 세트 예산 끝 한 점이고, 예산을 넘겨 끝난 시나리오는 해 없음으로 친다.

탐색 (루트 · 재라우팅 1 회가 수십 초) 은 중간에 끊을 수 없으므로, 탐색을 자식 프로세스에서 돌리고 부모가 시계를 본다:
  - 자식은 최선 해가 바뀔 때마다 (opt.stage3b on_best) 경로 목록을 부모에게 보낸다.
  - 부모는 마감 (예산 − 여유) 까지 기다린다. 자식이 먼저 끝나면 그 결과를, 마감이 오면 자식 (과 루트 작업자) 을 죽이고
    마지막으로 받은 최선 해를 돌려준다. 최선 해가 하나도 없으면 모든 배관을 경로 없음 (timeout = 예산 소진) 으로 돌려준다.
플래너가 돌려주는 시각 = 마감 이전이므로 pipeline 의 routing_time_sec ≤ 예산.
"""
import dataclasses
import multiprocessing as mp
import os
import signal
import time

from . import _sk  # noqa: F401
from pipe_routing.multi import PlanResult  # noqa: E402
from pipe_routing.router_astar import RouteResult  # noqa: E402

MARGIN_SEC = 2.0   # 마감 여유 — 자식 정리 · 결과 조립 시간 (`추정:` 측정으로 확인, 시험 opt/tests/test_deadline.py)


def _ser(routes: dict) -> dict:
    out = {}
    for pid, r in routes.items():
        d = dataclasses.asdict(r)
        d.pop("states", None)
        out[pid] = d
    return out


def _de(d: dict) -> dict:
    return {pid: RouteResult(**v) for pid, v in d.items()}


def _child(conn, run_search):
    os.setpgrp()                       # 자식 + 루트 작업자를 한 묶음으로 죽일 수 있게

    def on_best(st, score, point):
        conn.send(("best", {"routes": _ser(st.routes), "order": sorted(st.routes), "failed": dict(st.failed),
                            "score": list(score), "point": point}))
    try:
        final = run_search(on_best)
        conn.send(("done", final))
    except Exception as e:  # noqa: BLE001
        import traceback
        conn.send(("error", traceback.format_exc() + repr(e)))
    finally:
        conn.close()


def run_with_deadline(sc, run_search, budget_sec, margin=MARGIN_SEC):
    """run_search(on_best) → dict (자식에서 돈다). 반환 (PlanResult, info)."""
    t0 = time.perf_counter()
    deadline = t0 + budget_sec - margin
    ctx = mp.get_context("fork")
    rd, wr = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(wr, run_search))
    proc.start()
    wr.close()
    last, final, points, err, killed = None, None, [], None, False
    while True:
        rem = deadline - time.perf_counter()
        if rem <= 0:
            killed = True
            break
        if rd.poll(min(rem, 1.0)):
            try:
                kind, payload = rd.recv()
            except EOFError:
                break
            if kind == "best":
                last = payload
                points.append(payload["point"])
            elif kind == "done":
                final = payload
                break
            elif kind == "error":
                err = payload
                break
    if killed:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.join(timeout=5)
    stopped = time.perf_counter() - t0
    routes = _de(last["routes"]) if last else {}
    failed = last["failed"] if last else {}
    for p in sc.pipes:
        if p.id not in routes:
            why = failed.get(p.id, "")
            routes[p.id] = RouteResult(p.id, "timeout" if (last is None or why == "solo:timeout") else "unreachable")
    order = (last["order"] + sorted(last["failed"])) if last else sorted(routes)
    plan = PlanResult(routes=routes, order=order, planner="opt_priority_search", placement=list(order))
    info = {"killed_at_deadline": killed, "returned_sec": round(stopped, 3), "budget_sec": budget_sec,
            "margin_sec": margin, "has_solution": last is not None, "points": points, "final": final, "error": err}
    return plan, info
