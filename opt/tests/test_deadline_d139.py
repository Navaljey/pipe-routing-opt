"""D139 보완 (skeleton 55b0e35) 시험 — 판정 해 = 예산 끝 이전에 채점이 끝난 마지막 해 (opt/deadline.py).

라우팅 없이 가짜 탐색으로 시각을 정해 본다 (빠름). 예산 6 s · 여유 2 s → 마감 = 4 s.
  ① 1 s · 2 s 에 채점이 끝난 해 → 받는다. 5 s 에 끝난 해 → 받지 않는다 (마감 뒤). 반환 해 = 2 s 의 해.
  ② 3.9 s 에 끝난 해를 보내자마자 자식이 바쁘면 (읽기 전에 마감) → 죽인 뒤 읽어 받는다.
  ③ 해가 하나도 마감 전에 끝나지 않으면 해 없음 — 모든 배관 경로 없음.
  ④ 마감 뒤에 끝난 해를 보낸 경우 late_solutions 로 센다 (자식이 마감 뒤에도 살아 있다가 보낸 해를 부모가 먼저 읽는 경우).
"""
import time
from types import SimpleNamespace

from opt import _sk  # noqa: F401
from opt.deadline import run_with_deadline
from pipe_routing.router_astar import RouteResult


def _sc(n=3):
    return SimpleNamespace(pipes=[SimpleNamespace(id=f"P{i:03d}") for i in range(1, n + 1)])


def _st(tag):
    r = RouteResult("P001", "ok", waypoints=[[0, 0, 0], [tag * 1000.0, 0, 0]])
    return SimpleNamespace(routes={"P001": r}, failed={})


def _run(times, budget=6.0, margin=2.0, busy_after=None):
    t0 = time.time()

    def search(on_best):
        for k, t in enumerate(times, 1):
            dt = t0 + t - time.time()
            if dt > 0:
                time.sleep(dt)
            on_best(_st(k), (0, 1, float(k)), {"sec": t, "tag": k})
        if busy_after is not None:
            time.sleep(busy_after)
        return {"done": True}
    plan, info = run_with_deadline(_sc(), search, budget, margin=margin)
    return plan, info, time.time() - t0


def test_last_scored_before_deadline():
    plan, info, wall = _run([1.0, 2.0, 5.0])
    assert info["killed_at_deadline"] and info["has_solution"]
    assert [p["tag"] for p in info["points"]] == [1, 2]
    assert plan.routes["P001"].waypoints[1][0] == 2000.0
    assert wall < 6.0


def test_unread_before_deadline_is_taken():
    plan, info, _ = _run([3.9], busy_after=10)
    assert info["killed_at_deadline"]
    assert [p["tag"] for p in info["points"]] == [1]
    assert plan.routes["P001"].status == "ok"


def test_no_solution_before_deadline():
    plan, info, _ = _run([5.0])
    assert not info["has_solution"]
    assert all(r.status == "timeout" for r in plan.routes.values())


def test_late_solution_counted_not_used():
    # 여유 0.5 s · 예산 3 s → 마감 2.5 s. 해를 2.6 s 에 보내고 바로 끝나면 부모가 읽기 전에 마감이 지나도 받지 않는다.
    plan, info, _ = _run([1.0, 2.6], budget=3.0, margin=0.5)
    assert [p["tag"] for p in info["points"]] == [1]
    assert plan.routes["P001"].waypoints[1][0] == 1000.0
