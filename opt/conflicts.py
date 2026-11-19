"""루트 충돌 검출 3종 (CLAUDE.md §7.3, OPT.Dec12).

충돌 1 관 간섭: 루트 경로 쌍마다 multi.routes_conflict (검증기와 같은 식).
충돌 2 단자 예약 침범: 경로 ↔ 다른 배관 단자 예약 구간 (layered.terminal_reservations) — 루트에서 0 이어야 한다.
충돌 3 서포트 충돌: 루트 경로 전부를 함께 검증했을 때 support 위반이고, 단독 검증으로는 support 통과인 배관 p.
       책임 배관 q = skeleton support_diag 와 같은 방식 — p 근처(영향 범위 안) 배관 중 그 하나만 빼면 p 가 통과하는 배관,
       하나로 안 풀리면 빼면 서포트 불가 구간이 줄어드는 배관들 ("복수").
위반 없이 서포트가 무거워지는 경우는 충돌이 아니다 (OPT.Dec12).
"""
import time

from . import _sk  # noqa: F401
from pipe_routing.layered import terminal_reservations  # noqa: E402
from pipe_routing.multi import routes_conflict  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402
from pipe_routing.support_diag import _support_of  # noqa: E402
from pipe_routing.support_std import load_standard  # noqa: E402
from pipe_routing.verifier import PipeRoute, verify  # noqa: E402
from pipe_routing.verifier.core import centerlines_of  # noqa: E402
from pipe_routing.verifier.geom import segment_segment_distance  # noqa: E402
from pipe_routing.verifier.session import _reach, _segs  # noqa: E402


def detect(path: str, root: dict) -> dict:
    t0 = time.perf_counter()
    sc = load(path)
    std = load_standard()
    pipes = {p.id: p for p in sc.pipes}
    ok = {x["pipe_id"]: x["waypoints"] for x in root["pipes"] if x["status"] == "ok"}
    ids = sorted(ok)

    # 충돌 1
    c1 = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]
          if routes_conflict(pipes[a], ok[a], pipes[b], ok[b])]
    t1 = time.perf_counter()

    # 충돌 2
    c2 = []
    for a in ids:
        for q, seg in terminal_reservations(sc, exclude={a}):
            if routes_conflict(pipes[a], ok[a], q, seg):
                c2.append((a, q.id))
    t2 = time.perf_counter()

    # 단독 검증 (배관마다 혼자) · 함께 검증
    solo = {}
    for a in ids:
        rep = verify(sc, [PipeRoute(a, ok[a])], std=std)["pipes"][a]
        solo[a] = {"pass": rep.success, "support": rep.layer0.get("support"),
                   "failed": sorted(k for k, v in rep.layer0.items() if v is False)}
    multi = verify(sc, [PipeRoute(a, ok[a]) for a in ids], std=std)
    mrep = multi["pipes"]
    t3 = time.perf_counter()

    # 충돌 3
    routes = {a: PipeRoute(a, ok[a]) for a in ids}
    cls = {a: centerlines_of(pipes[a], routes[a]) for a in ids}
    segs = {a: _segs(c) for a, c in cls.items()}
    reach = _reach(std)
    c3, c3_rows = [], []
    for p in ids:
        if mrep[p].layer0.get("support") is not False or solo[p]["support"] is False:
            continue
        A0, A1, AS = segs[p]
        near, dist = [], {}
        for q in ids:
            if q == p:
                continue
            B0, B1, BS = segs[q]
            d, _, _ = segment_segment_distance(A0[:, None], A1[:, None], B0[None], B1[None])
            dist[q] = float((d - AS[:, None] - BS[None]).min())
            if dist[q] <= pipes[p].radius + pipes[q].radius + reach:
                near.append(q)
        base_v, _ = _support_of(sc, pipes, routes, cls, std, p)
        single, reducing = [], []
        for q in sorted(near, key=lambda q: (dist[q], q)):
            v, _ = _support_of(sc, pipes, routes, cls, std, p, exclude=(q,))
            if not v:
                single.append(q)
            elif len(v) < len(base_v):
                reducing.append(q)
        blockers = single or reducing
        c3_rows.append({"pipe": p, "blockers": blockers,
                        "kind": "single" if single else ("multiple" if reducing else "none")})
        c3 += [(p, q) for q in blockers]
    t4 = time.perf_counter()

    unordered = lambda prs: {tuple(sorted(x)) for x in prs}  # noqa: E731
    involved = {x for pr in c1 + c2 + c3 for x in pr}
    return {
        "routed": len(ids),
        "conflict1_pairs": sorted(c1), "conflict2_pairs": sorted(c2), "conflict3": c3_rows,
        "conflict3_pairs": sorted(c3),
        "pairs_any": len(unordered(c1) | unordered(c2) | unordered(c3)),
        "pipes_in_conflict": sorted(involved),
        "solo_pass": sum(1 for a in ids if solo[a]["pass"]),
        "solo_fail_modules": {a: solo[a]["failed"] for a in ids if not solo[a]["pass"]},
        "multi_pass": sum(1 for a in ids if mrep[a].success),
        "support_standard": multi["support_standard"],
        "sec": {"c1": t1 - t0, "c2": t2 - t1, "verify": t3 - t2, "c3": t4 - t3, "total": t4 - t0},
    }
