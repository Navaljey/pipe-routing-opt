"""루트 단독 경로 (CLAUDE.md §7.2, OPT.Dec6 · Dec10 · Dec16 · Req2 · Req6 · Req7).

배관마다 기준선 라우터로 단독 라우팅 — placed = () 이면 다른 배관의 단자 예약 (D59) 은 지키고 부재 예약 (D90) 은
붙지 않는다 (OPT.Stage1 1차 조사). D64 · D80 ⑩ · D106 (b) 규칙상 불가능 배관은 라우팅하지 않고 분모에서 뺀다.

병렬 (workers ≥ 2): 고정층 캐시가 (구경, 중력관 층) 별이라 그 묶음 단위로 작업자에 나눈다 (결정적 LPT 분배).
결과는 작업자 · 완료 순서와 무관하게 배관 id 순으로 합친다 (OPT.Req6). 같은 배관의 라우팅 결과는 어느 프로세스에서
돌든 같다 (고정층은 시나리오에서만 정해지고, 증분 표시는 전체 재계산과 비트 동일 — skeleton D67).
"""
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
import multiprocessing as mp

from . import _sk  # noqa: F401
from pipe_routing.layered import default_budget, layered_router_a  # noqa: E402
from pipe_routing.pipeline import required_pipe_ids  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402

TIME_LIMIT = 60.0   # 벽시계 제한은 쓰이지 않는다 — 확장 상한이 있으면 astar 가 벽시계를 보지 않는다 (skeleton D72)


@dataclass
class RootPipe:
    pipe_id: str
    status: str                 # ok · unreachable · timeout(= 상한 소진) · excluded
    waypoints: list = field(default_factory=list)
    expanded: int = 0
    J_router: float | None = None
    search_sec: float = 0.0
    graph_sec: float = 0.0


def _group_key(p):
    return (p.nominal_size, bool(p.gravity_pipe))


def _route_ids(path: str, ids: list) -> tuple:
    """작업자 1개: 시나리오를 직접 읽고 ids 를 차례로 라우팅. (결과 목록, CPU 초, 벽시계 초)."""
    sc = load(path)
    pipes = {p.id: p for p in sc.pipes}
    c0, w0 = time.process_time(), time.perf_counter()
    out = []
    for pid in ids:
        r = layered_router_a(sc, pipes[pid], TIME_LIMIT, placed=())
        out.append(RootPipe(pid, r.status, [list(map(float, w)) for w in (r.waypoints or [])] if r.status == "ok" else [],
                            int(r.expanded or 0), (float(r.J) if r.status == "ok" and r.J is not None else None),
                            float(r.search_sec), float(getattr(r, "graph_sec", 0.0) or 0.0)))
    return out, time.process_time() - c0, time.perf_counter() - w0


def assign(sc, ids: list, workers: int) -> list:
    """(구경, 중력관 층) 묶음을 배관 수 큰 순 → 키 순으로, 지금 배관 수가 가장 적은 작업자(같으면 번호 작은 쪽)에 준다."""
    pipes = {p.id: p for p in sc.pipes}
    groups = {}
    for pid in ids:
        groups.setdefault(_group_key(pipes[pid]), []).append(pid)
    order = sorted(groups, key=lambda k: (-len(groups[k]), int(k[0][:-1]), k[1]))
    bins = [[] for _ in range(workers)]
    for k in order:
        i = min(range(workers), key=lambda j: (len(bins[j]), j))
        bins[i].extend(groups[k])
    return [sorted(b) for b in bins if b]


def build_root(path: str, workers: int = 1) -> dict:
    sc = load(path)
    t0 = time.perf_counter()
    req, exc = required_pipe_ids(sc)
    ids = sorted(req)
    cpu_main0 = time.process_time()
    if workers <= 1:
        res, cpu, wall = _route_ids(path, ids)
        parts, cpu_workers = [ids], [cpu]
    else:
        parts = assign(sc, ids, workers)
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=len(parts), mp_context=ctx) as ex:
            futs = [ex.submit(_route_ids, path, b) for b in parts]
            got = [f.result() for f in futs]
        res = [x for g in got for x in g[0]]
        cpu_workers = [g[1] for g in got]
    wall = time.perf_counter() - t0
    by = {x.pipe_id: x for x in res}
    for pid in exc:
        by[pid] = RootPipe(pid, "excluded")
    pipes = [by[p.id] for p in sorted(sc.pipes, key=lambda p: p.id)]
    return {
        "scenario": path, "workers": workers, "parts": parts, "excluded": sorted(exc),
        "budget_per_route": default_budget(sc),
        "root_wall_sec": wall, "cpu_sec_workers": cpu_workers,
        "cpu_sec_main": time.process_time() - cpu_main0,
        "max_concurrent_cores": max(1, min(workers, len(parts))),
        "expanded_total": sum(x.expanded for x in pipes),
        "pipes": [x.__dict__ for x in pipes],
    }


def deterministic_view(root: dict) -> list:
    """판정에 쓰는 부분 (상태 · 경로 · 확장 수 · 라우터 J) — 시간 · 작업자 배치 제외 (OPT.Req6 시험용)."""
    return [(x["pipe_id"], x["status"], x["waypoints"], x["expanded"], x["J_router"]) for x in root["pipes"]]
