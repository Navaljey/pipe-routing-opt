"""OPT.Stage3 — 우선순위 탐색 + 충돌 3 (서포트 충돌, OPT.Dec12) + anytime (§7.7).

OPT.Stage2 (`opt.priority.Search`) 위에 더한 것:
  ① 충돌 3: 충돌 1 · 2 가 0 이 된 노드를 검증기로 채점한다. support 위반이면서 단독 검증으로는 support 를 통과하는
     배관 p 와, 그 하나만 빼면 p 가 통과하는 배관 q (없으면 빼서 불가 구간이 줄어드는 배관들) 를 쌍 (p, q) 로 등록한다
     (OPT.Stage1 `opt/conflicts.py` 와 같은 판정, OPT.Req13). 충돌 3 도 (가) / (나) 두 자식으로 푼다.
  ② anytime: 첫 해에 닿으면 채점해 곡선에 점을 찍고, 남은 예산으로 내려오며 남겨 둔 다른 갈래를 최근 것부터
     (깊이 우선 되돌아가기) 다시 풀어 해 후보를 찾는다. D77 사전식 (완전 성공, 통과 수, −J_total) 이 좋아질 때만
     최선 해를 바꾼다.
  ③ OPT.Dec22: 첫 해 뒤, 단독 루트에서 상한을 소진한 배관을 놓인 배관 전부를 placed 로 한 번 더 라우팅한다.
     성공하면 우선순위 최하위로 넣는다 (이후 되돌아간 갈래에도 같은 경로로 넣는다).
  ④ OPT.Dec23: 충돌 고르기 규칙 = 고정 (큰 구경 → id) 또는 이력 (배관 쌍 반복 횟수 큰 쪽 → 고정 규칙).
  ⑤ OPT.Dec24: 다시 깐 자식에서 고른 충돌이 그대로면 버린다 (충돌 3 은 p 의 support 판정으로 확인).
  ⑥ OPT.Dec26 ①: 충돌마다 두 갈래의 결과를 학습 기록으로 남긴다 (식별자 없이 성질 · 관계만, OPT.Dec25).

끌어당김 · LNS 는 넣지 않는다 (OPT.Stage4 · OPT.Dec27).
"""
import time

from . import _sk  # noqa: F401
from .priority import Search, State, rcost, size_mm
from pipe_routing.multi import _route, routes_conflict  # noqa: E402
from pipe_routing.pipeline import j_breakdown, required_pipe_ids  # noqa: E402
from pipe_routing.support_diag import _support_of  # noqa: E402
from pipe_routing.support_std import load_standard  # noqa: E402
from pipe_routing.verifier import PipeRoute, verify  # noqa: E402
from pipe_routing.verifier.core import centerlines_of  # noqa: E402
from pipe_routing.verifier.geom import segment_segment_distance  # noqa: E402
from pipe_routing.verifier.session import _reach, _segs  # noqa: E402


def better(a, b) -> bool:
    """D77 사전식: (완전 성공, 통과 수, −J). b 가 None 이면 참."""
    if b is None:
        return True
    if a[0] != b[0]:
        return a[0] > b[0]
    if a[1] != b[1]:
        return a[1] > b[1]
    return a[2] < b[2] - 1e-6


class Search3(Search):
    def __init__(self, sc, router, time_limit, budget_sec=None, log=None, rule="fixed", learn=None, first_only=False):
        super().__init__(sc, router, time_limit, budget_sec, log)
        self.first_only = first_only
        self.rule = rule
        self.hist = {}
        self.std = load_standard()
        self.reach = _reach(self.std)
        self.required = set(required_pipe_ids(sc)[0])
        self.learn = learn            # list 이면 학습 기록을 쌓는다
        self.stack = []               # 남겨 둔 갈래: (State, 학습 행 id, 갈래 이름)
        self.cur_rows = []            # 지금 내려가는 길에서 만든 학습 행 (고른 갈래)
        self.expanded = 0
        self.extra = {}               # OPT.Dec22 로 다시 시도해 성공한 배관 → RouteResult
        self.n_verify = 0

    # ------------------------------------------------------------------ 라우팅 · 기록
    def reroute(self, st_routes, prec, pid, why):
        r = super().reroute(st_routes, prec, pid, why)
        self.expanded += int(r.expanded or 0)
        return r

    def pick(self, cs):
        def key(c):
            a, b, _ = c
            h = self.hist.get((a, b) if a < b else (b, a), 0) if self.rule == "history" else 0
            return (-h, -max(size_mm(self.pipes[a]), size_mm(self.pipes[b])), a, b)
        return min(cs, key=key)

    def count_history(self, cs):
        for a, b, _ in cs:
            k = (a, b) if a < b else (b, a)
            self.hist[k] = self.hist.get(k, 0) + 1

    def pipe_feat(self, pid, st):
        p = self.pipes[pid]
        r = st.routes.get(pid)
        w = r.waypoints if r is not None else []
        length = sum(sum(abs(a - b) for a, b in zip(u, v)) for u, v in zip(w, w[1:])) if w else None
        return {"nominal_mm": size_mm(p), "gravity": bool(p.gravity_pipe), "insulation_mm": p.insulation_thickness,
                "type": p.type_id, "radius_mm": round(p.radius, 2),
                "bends": max(0, len(w) - 2) if w else None, "path_mm": round(length, 1) if length else None,
                "n_ancestors": len(self.ancestors(st.prec, pid)), "n_descendants": len(self.descendants(st.prec, pid))}

    # ------------------------------------------------------------------ 충돌 3
    def score_and_support_conflicts(self, st):
        """검증기로 지금 배치를 채점하고 충돌 3 쌍을 찾는다. 반환 (점수, 통과 id, rep, [(p, q, 3)])."""
        ids = sorted(st.routes)
        routes = {a: PipeRoute(a, st.routes[a].waypoints) for a in ids}
        rep = verify(self.sc, list(routes.values()), std=self.std)["pipes"]
        self.n_verify += 1
        passed = {a for a in ids if rep[a].success}
        J = sum(j_breakdown(self.pipes[a], st.routes[a].waypoints, rep[a].fittings, rep[a].supports, self.sc)["total"]
                for a in passed)
        full = int(all(a in passed for a in self.required))
        cls = {a: centerlines_of(self.pipes[a], routes[a]) for a in ids}
        segs = {a: _segs(c) for a, c in cls.items()}
        out = []
        for p in ids:
            if rep[p].layer0.get("support") is not False:
                continue
            solo_v, _ = _support_of(self.sc, self.pipes, routes, cls, self.std, p, exclude=tuple(q for q in ids if q != p))
            if solo_v:
                continue          # 단독으로도 support 위반 — 서포트 충돌 아님
            A0, A1, AS = segs[p]
            near, dist = [], {}
            for q in ids:
                if q == p:
                    continue
                B0, B1, BS = segs[q]
                d, _, _ = segment_segment_distance(A0[:, None], A1[:, None], B0[None], B1[None])
                dist[q] = float((d - AS[:, None] - BS[None]).min())
                if dist[q] <= self.pipes[p].radius + self.pipes[q].radius + self.reach:
                    near.append(q)
            base_v, _ = _support_of(self.sc, self.pipes, routes, cls, self.std, p)
            single, reducing = [], []
            for q in sorted(near, key=lambda q: (dist[q], q)):
                v, _ = _support_of(self.sc, self.pipes, routes, cls, self.std, p, exclude=(q,))
                if not v:
                    single.append(q)
                elif len(v) < len(base_v):
                    reducing.append(q)
            for q in (single or reducing):
                out.append((p, q, 3))
        return (full, len(passed), round(J, 6)), passed, rep, out

    def support_blocked_by(self, st, p, q) -> bool:
        """OPT.Dec24 (충돌 3): p 가 지금 support 위반이고 q 를 빼면 통과하는가."""
        ids = sorted(st.routes)
        if p not in st.routes or q not in st.routes:
            return False
        routes = {a: PipeRoute(a, st.routes[a].waypoints) for a in ids}
        cls = {a: centerlines_of(self.pipes[a], routes[a]) for a in ids}
        v, _ = _support_of(self.sc, self.pipes, routes, cls, self.std, p)
        if not v:
            return False
        v2, _ = _support_of(self.sc, self.pipes, routes, cls, self.std, p, exclude=(q,))
        return len(v2) < len(v)

    # ------------------------------------------------------------------ 한 단계 (충돌 종류 공통)
    def step(self, st, cs, stats):
        """충돌 목록 cs 에서 하나를 골라 푼다. 다른 갈래는 stack 에 남긴다. 다음 State 를 돌려준다."""
        self.count_history(cs)
        p, q, kind = self.pick(cs)
        stats["steps"] += 1
        row = None
        if self.learn is not None:
            row = {"step": len(self.learn), "kind": kind, "rule": self.rule,
                   "p": self.pipe_feat(p, st), "q": self.pipe_feat(q, st),
                   "rel": {"p_anc_of_q": p in self.ancestors(st.prec, q), "q_anc_of_p": q in self.ancestors(st.prec, p),
                           "history": self.hist.get((p, q) if p < q else (q, p), 0),
                           "n_conflicts_now": len(cs)},
                   "branches": {}, "_ids": [p, q]}
            self.learn.append(row)
        kids = []
        for a, b, tag in ((p, q, "ga"), (q, p, "na")):
            t0, e0, n0 = time.perf_counter(), self.expanded, len(self.reroutes)
            k = self.child(st, a, b)
            status = "ok" if k is not None else ("cycle" if a in self.descendants(st.prec, b) else "no_route")
            if k is not None:
                still = (self.support_blocked_by(k, p, q) if kind == 3
                         else (p, q, kind) in self.conflicts(k, {p, q}))
                if still:
                    self.log.setdefault("unresolved_child", []).append([p, q, kind, tag])
                    k, status = None, "unresolved"
            cost = sum(rcost(r) for r in k.routes.values()) if k is not None else None
            if row is not None:
                row["branches"][tag] = {"yield": "q" if b == q else "p", "status": status, "router_cost": cost,
                                        "router_cost_delta": (cost - sum(rcost(r) for r in st.routes.values()))
                                        if cost is not None else None,
                                        "n_rerouted": len(self.reroutes) - n0, "sec": round(time.perf_counter() - t0, 3),
                                        "expanded": self.expanded - e0, "outcome": None}
            if k is not None:
                kids.append((cost, 0 if tag == "ga" else 1, tag, k))
        if kids:
            kids.sort(key=lambda x: (x[0], x[1]))
            cost, _, tag, nxt = kids[0]
            for c2, _, tag2, other in kids[1:]:
                self.stack.append((other, row["step"] if row else None, tag2))
            if row is not None:
                row["chosen"] = tag
                self.cur_rows.append((row["step"], tag))
            self.res.events.append(f"step {stats['steps']}: ({p},{q}) kind{kind} → {tag} children={len(kids)} cost={cost:.1f}")
            stats["children_alive"].append(len(kids))
            return nxt
        x = self.drop_one(p, q)
        routes = dict(st.routes)
        routes.pop(x)
        failed = dict(st.failed)
        failed[x] = "coexist"
        prec = {(a, b) for a, b in st.prec if x not in (a, b)}
        if row is not None:
            row["chosen"] = "drop"
            row["dropped"] = "p" if x == p else "q"
        self.res.events.append(f"step {stats['steps']}: ({p},{q}) kind{kind} 함께 놓을 수 없음 → {x} 실패")
        stats["dropped"].append(x)
        return State(routes, prec, failed)

    def solve_cluster(self, st, members, stats):
        while not self.out_of_budget():
            cs = self.conflicts(st, members)
            if not cs:
                return st
            st = self.step(st, cs, stats)
        return st

    # ------------------------------------------------------------------ 해 후보까지 내려가기
    def dive(self, st):
        rounds = []
        while not self.out_of_budget():
            cs = self.conflicts(st)
            if cs:
                cl = self.clusters(cs)
                rnd = {"conflicts": len(cs), "clusters": [len(c) for c in cl], "steps": 0, "dropped": [],
                       "children_alive": [], "kind": 12}
                for c in cl:
                    if self.out_of_budget():
                        break
                    st = self.solve_cluster(st, c, rnd)
                rounds.append(rnd)
                continue
            score, passed, rep, c3 = self.score_and_support_conflicts(st)
            if not c3:
                return st, score, passed, rounds
            rnd = {"conflicts": len(c3), "clusters": [], "steps": 0, "dropped": [], "children_alive": [], "kind": 3}
            st = self.step(st, c3, rnd)
            rounds.append(rnd)
        return st, None, None, rounds

    def inject_extra(self, st):
        """OPT.Dec22 로 얻은 경로를 우선순위 최하위로 넣는다."""
        if not self.extra:
            return st
        routes, failed, prec = dict(st.routes), dict(st.failed), set(st.prec)
        for pid, r in self.extra.items():
            if failed.get(pid, "").startswith("solo:"):
                failed.pop(pid)
                for a in list(routes):
                    prec.add((a, pid))
                routes[pid] = r
        return State(routes, prec, failed)

    def retry_timeouts(self, st):
        """OPT.Dec22: 단독 루트에서 상한을 소진한 배관을 놓인 배관 전부를 placed 로 한 번 더."""
        ids = sorted(pid for pid, why in st.failed.items() if why == "solo:timeout")
        out = {}
        for pid in ids:
            if self.out_of_budget():
                break
            placed = [(self.pipes[a], st.routes[a].waypoints) for a in sorted(st.routes)]
            t = time.perf_counter()
            r = _route(self.res, self.sc, self.pipes[pid], self.router, self.tl, placed)
            self.expanded += int(r.expanded or 0)
            self.reroutes.append({"pipe": pid, "status": r.status, "sec": round(time.perf_counter() - t, 3),
                                  "expanded": int(r.expanded or 0), "n_placed": len(placed), "why": "retry_timeout"})
            if r.status == "ok":
                out[pid] = r
        self.log["retry_timeout"] = {"tried": ids, "ok": sorted(out)}
        return out

    def interference_block(self, st, root_routes):
        ok = sorted(st.routes)
        n = 0
        for pid, r in root_routes.items():
            if pid in st.routes or r.status != "ok":
                continue
            if any(routes_conflict(self.pipes[pid], r.waypoints, self.pipes[q], st.routes[q].waypoints) for q in ok):
                n += 1
        return n

    def anytime(self, root) -> dict:
        routes = {pid: r for pid, r in root["routes"].items() if r.status == "ok"}
        failed = {pid: f"solo:{r.status}" for pid, r in root["routes"].items() if r.status != "ok"}
        for pid in root["excluded"]:
            failed[pid] = "excluded"
        st = State(routes, set(), failed)
        self.expanded += sum(int(r.expanded or 0) for r in root["routes"].values())
        best, best_score, points, dives, first = None, None, [], [], None
        retried = False
        origin, last_tag = None, None
        while True:
            self.cur_rows = []
            st_end, score, passed, rounds = self.dive(st)
            dives.append({"rounds": rounds, "score": score, "origin": origin})
            if score is not None and self.learn is not None:
                for rid, tag in self.cur_rows + ([origin] if origin and origin[0] is not None else []):
                    p, q = self.learn[rid]["_ids"]
                    self.learn[rid]["branches"][tag]["outcome"] = {
                        "p_pass": p in passed, "q_pass": q in passed, "candidate": list(score)}
            if score is not None:
                kind = "first" if first is None else ("retry_timeout" if last_tag == "retry" else "backtrack")
                if first is None:
                    first = (st_end, score, time.perf_counter() - self.t0, self.expanded)
                if better(score, best_score):
                    best, best_score = st_end, score
                    pt = {"routed": len(st_end.routes), "success": score[1],
                          "success_rate": round(score[1] / len(self.sc.pipes), 4), "J_total": round(score[2], 3),
                          "interference_block": self.interference_block(st_end, root["routes"]),
                          "full_success": score[0], "required_passed": len(self.required & passed),
                          "expanded": self.expanded, "sec": round(time.perf_counter() - self.t0, 3), "move": kind}
                    points.append(pt)
                if not retried and not self.first_only:   # OPT.Dec22 — 첫 해 뒤 한 번
                    retried = True
                    self.extra = self.retry_timeouts(st_end)
                    if self.extra:
                        self.stack.append((st_end, None, "retry"))
            if self.out_of_budget() or not self.stack or (self.first_only and first is not None):
                break
            st0, rid, tag = self.stack.pop()
            origin = (rid, tag) if rid is not None else None
            st = self.inject_extra(st0)
            last_tag = tag
        if best is None:
            best = st_end
        self.log["dives"] = dives
        self.log["stop"] = "budget" if self.exhausted else "stack_empty"
        self.log["first"] = {"score": list(first[1]), "sec": round(first[2], 3), "expanded": first[3]} if first else None
        self.log["n_verify"] = self.n_verify
        self.log["stack_left"] = len(self.stack)
        return {"best": best, "best_score": best_score, "points": points, "first": first}


def make_planner3(budget_sec, rule="fixed", root_fn=None, log=None, learn=None, keep=None, first_only=False):
    """pipeline.run(planner=...) 슬롯. keep = dict — 첫 해 State 와 곡선 점을 돌려받는다 (출력 · 곡선 파일용)."""
    log = log if log is not None else {}
    keep = keep if keep is not None else {}

    def planner(sc, router, time_limit):
        from pipe_routing.router_astar import RouteResult
        S = Search3(sc, router, time_limit, budget_sec, log, rule=rule, learn=learn, first_only=first_only)
        keep["search"] = S
        t = time.perf_counter()
        if root_fn is None:
            req, exc = required_pipe_ids(sc)
            root = {"routes": {pid: _route(S.res, sc, S.pipes[pid], router, time_limit, []) for pid in sorted(req)},
                    "excluded": sorted(exc)}
        else:
            root = root_fn(sc, router, time_limit, S.res)
        log["root_sec"] = time.perf_counter() - t
        log["root_status"] = {pid: r.status for pid, r in sorted(root["routes"].items())}
        out = S.anytime(root)
        log["search_sec"] = time.perf_counter() - t - log["root_sec"]
        log["reroutes"] = S.reroutes
        log["budget_exhausted"] = S.exhausted
        keep.update(out)

        def to_plan(st):
            res = S.res
            routes = {}
            for p in sc.pipes:
                r = st.routes.get(p.id)
                if r is None:
                    r0 = root["routes"].get(p.id)
                    r = r0 if (r0 is not None and r0.status != "ok") else RouteResult(p.id, "unreachable")
                routes[p.id] = r
            order = S.topo(st.prec, list(st.routes)) + sorted(st.failed)
            from pipe_routing.multi import PlanResult
            pr = PlanResult(routes=routes, order=order, planner="opt_priority_search", placement=list(order),
                            events=list(res.events), attempts=res.attempts)
            pr.total_sec = time.perf_counter() - S.t0
            return pr
        keep["first_plan"] = to_plan(out["first"][0]) if out["first"] else None
        log["prec"] = sorted(out["best"].prec)
        log["failed"] = dict(sorted(out["best"].failed.items()))
        return to_plan(out["best"])

    planner.log = log
    planner.keep = keep
    return planner
