"""OPT.Stage2 — 우선순위 탐색 (CLAUDE.md §7.4 · §7.5, OPT.Dec5 · Dec7 · Dec17), 충돌 1 · 2 만, 첫 해까지.

흐름
  ① 루트 (§7.2): 배관마다 단독 라우팅 (placed = (), 다른 배관 단자 예약 지킴). 규칙상 불가능 배관은 분모 제외,
     단독 실패 배관은 탐색 대상에서 뺀다 (OPT.Req2).
  ② 충돌 그래프 덩어리 (연결 성분) 별로 깊이 우선으로 내려가 해에 닿는다 (OPT.Dec17). 덩어리 안에서는 덩어리
     배관끼리의 충돌만 본다.
  ③ 덩어리를 모두 푼 뒤 전체 충돌을 다시 검사한다. 새 충돌이 있으면 그 충돌로 새 덩어리를 만들어 ② 를 반복한다.

한 단계 (§7.4)
  - 충돌 쌍 고르기 `추정:` = 큰 구경을 포함한 쌍 먼저 → 같으면 (배관 id 사전순).
  - 자식 (가) p ≺ q: q 를 다시 깐다 (placed = q 의 모든 조상). 그다음 q 의 자손 중 새 경로와 충돌하는 배관을
    위상 순서로 다시 깐다 (다시 깐 배관의 자손도 같은 규칙으로 이어서). 자식 (나) q ≺ p 는 반대.
    순환이 생기는 자식은 만들지 않는다. 다시 깔다가 경로를 못 찾으면 그 자식은 버린다.
  - 남은 자식 중 라우터 비용 합 (직관 + 엘보 + 추정 서포트 + 관통) 이 작은 쪽으로 내려간다. 같으면 (가).
  - 두 자식이 다 버려지면 (§7.5) 호칭경이 작은 쪽을 실패로 둔다 (OPT.Dec7). 같으면 multi.default_order 에서
    뒤에 오는 배관 (`추정:`). 실패로 둔 배관은 이 갈래 아래에서 다시 시도하지 않는다.
  - 첫 해까지는 되돌아가기가 필요 없다: 매 단계 (가)·(나) 중 하나가 남거나, 없으면 실패 갈래가 있다.
    서로 관계 없는 쌍에만 새 ≺ 가 생기므로 (관계 있는 쌍의 충돌은 다시 깔기 연쇄가 푼다) 단계 수는 유한하다.

출력은 배관별 경로 목록 (PlanResult.routes) 이다. ≺ 는 기록 (events · opt_log) 에만 남기며, 나중 개선 단계가
≺ 없이 받을 수 있게 둔다 (OPT.Question19).
예산: §7.7 의 벽시계 판정 예산을 상한으로만 쓴다 — 상한 안에서 끝나면 결과는 결정적이다 (OPT.Req6).
"""
import time
from dataclasses import dataclass, field

from . import _sk  # noqa: F401
from pipe_routing.layered import terminal_reservations  # noqa: E402
from pipe_routing.multi import PlanResult, _route, default_order, routes_conflict  # noqa: E402
from pipe_routing.pipeline import required_pipe_ids  # noqa: E402
from pipe_routing.router_astar import RouteResult  # noqa: E402

SET_BUDGET_SEC = {"base": 900, "stair": 900, "dense30": 3600, "dense50": 7200, "pair": 3600}   # §2.1 판정 예산


def size_mm(p) -> int:
    return int(p.nominal_size[:-1])


def rcost(r) -> float:
    return float((r.J or 0.0) + (r.J_support_est or 0.0) + (getattr(r, "J_penetration", 0.0) or 0.0))


@dataclass
class State:
    routes: dict                       # pid → RouteResult (ok 만)
    prec: set = field(default_factory=set)      # (a, b) = a ≺ b
    failed: dict = field(default_factory=dict)  # pid → 이유 ("solo:<status>" · "excluded" · "coexist")


class Search:
    def __init__(self, sc, router, time_limit, budget_sec=None, log=None):
        self.sc, self.router, self.tl = sc, router, time_limit
        self.pipes = {p.id: p for p in sc.pipes}
        self.order_rank = {p.id: i for i, p in enumerate(default_order(sc))}
        self.res = PlanResult(routes={}, order=[], planner="opt_priority_search")
        self.budget = budget_sec
        self.t0 = time.perf_counter()
        self._cc = {}                  # 충돌 판정 캐시 (경로 쌍)
        self._resv = {}                # 배관별 단자 예약
        self._rc = {}                  # 충돌 2 캐시
        self.reroutes = []             # 재라우팅 기록
        self.log = log if log is not None else {}
        self.exhausted = False

    # ------------------------------------------------------------------ 판정
    def conflict(self, a, wa, b, wb) -> bool:
        key = (a, id(wa), b, id(wb)) if a < b else (b, id(wb), a, id(wa))
        if key not in self._cc:
            self._cc[key] = (wa, wb, routes_conflict(self.pipes[a], wa, self.pipes[b], wb))   # wa·wb 를 붙잡아 id 재사용 방지
        return self._cc[key][2]

    def reservations(self, a):
        if a not in self._resv:
            self._resv[a] = terminal_reservations(self.sc, exclude={a})
        return self._resv[a]

    def resv_hits(self, a, wa) -> tuple:
        key = (a, id(wa))
        if key not in self._rc:
            self._rc[key] = (wa, tuple(q.id for q, seg in self.reservations(a)
                                       if routes_conflict(self.pipes[a], wa, q, seg)))
        return self._rc[key][1]

    def conflicts(self, st: State, members=None) -> list:
        """충돌 1 (관 간섭) · 충돌 2 (단자 예약 침범) 쌍 목록 [(a, b, 종류)], a < b. members 가 있으면 그 안에서만."""
        ids = sorted(st.routes if members is None else [m for m in members if m in st.routes])
        out = []
        for i, a in enumerate(ids):
            wa = st.routes[a].waypoints
            for b in ids[i + 1:]:
                if self.conflict(a, wa, b, st.routes[b].waypoints):
                    out.append((a, b, 1))
        have = {x[:2] for x in out}
        for a in ids:   # 충돌 2: 경로 ↔ 아직 놓이지 않은 배관의 단자 예약 (D59 — 놓인 배관의 예약은 실제 경로로 대체,
            #           실패로 둔 배관은 놓이지 않는다). 모든 배관이 경로를 가진 상태에서는 대상이 없다 — 확인만
            for qid in self.resv_hits(a, st.routes[a].waypoints):
                k = tuple(sorted((a, qid)))
                if qid not in st.failed and qid not in st.routes and k not in have:
                    out.append(k + (2,))
                    have.add(k)
        return out

    # ------------------------------------------------------------------ ≺
    @staticmethod
    def ancestors(prec, q) -> set:
        par = {}
        for a, b in prec:
            par.setdefault(b, set()).add(a)
        seen, stack = set(), [q]
        while stack:
            for a in par.get(stack.pop(), ()):
                if a not in seen:
                    seen.add(a)
                    stack.append(a)
        return seen

    @staticmethod
    def descendants(prec, q) -> set:
        ch = {}
        for a, b in prec:
            ch.setdefault(a, set()).add(b)
        seen, stack = set(), [q]
        while stack:
            for b in ch.get(stack.pop(), ()):
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        return seen

    @staticmethod
    def topo(prec, nodes) -> list:
        nodes = set(nodes)
        indeg = {n: 0 for n in nodes}
        for a, b in prec:
            if a in nodes and b in nodes:
                indeg[b] += 1
        out, ready = [], sorted(n for n in nodes if indeg[n] == 0)
        while ready:
            n = ready.pop(0)
            out.append(n)
            for a, b in sorted(prec):
                if a == n and b in nodes:
                    indeg[b] -= 1
                    if indeg[b] == 0:
                        ready.append(b)
                        ready.sort()
        return out

    # ------------------------------------------------------------------ 다시 깔기
    def out_of_budget(self) -> bool:
        if self.budget is not None and time.perf_counter() - self.t0 > self.budget:
            self.exhausted = True
        return self.exhausted

    def reroute(self, st_routes, prec, pid, why):
        anc = self.ancestors(prec, pid)
        placed = [(self.pipes[a], st_routes[a].waypoints) for a in sorted(anc) if a in st_routes]
        t = time.perf_counter()
        r = _route(self.res, self.sc, self.pipes[pid], self.router, self.tl, placed)
        self.reroutes.append({"pipe": pid, "status": r.status, "sec": round(time.perf_counter() - t, 3),
                              "expanded": int(r.expanded or 0), "n_placed": len(placed), "why": why})
        return r

    def child(self, st: State, p, q):
        """p ≺ q 를 더한 자식. 실패면 None."""
        if p in self.descendants(st.prec, q):     # q ≺ … ≺ p 이면 순환
            return None
        prec = set(st.prec) | {(p, q)}
        routes = dict(st.routes)
        r = self.reroute(routes, prec, q, f"{p}<{q}")
        if r.status != "ok":
            return None
        routes[q] = r
        # q 의 자손을 위상 순서로 한 번 훑는다 — 조상이 먼저 확정되므로 한 번이면 된다
        for d in self.topo(prec, self.descendants(prec, q)):
            if d not in routes:
                continue
            anc = self.ancestors(prec, d)
            if any(a in routes and self.conflict(a, routes[a].waypoints, d, routes[d].waypoints) for a in anc):
                r = self.reroute(routes, prec, d, f"cascade<{q}")
                if r.status != "ok":
                    return None
                routes[d] = r
        return State(routes, prec, dict(st.failed))

    # ------------------------------------------------------------------ 탐색
    def pick(self, cs):
        def key(c):
            a, b, _ = c
            return (-max(size_mm(self.pipes[a]), size_mm(self.pipes[b])), a, b)
        return min(cs, key=key)

    def drop_one(self, p, q):
        sp, sq = size_mm(self.pipes[p]), size_mm(self.pipes[q])
        if sp != sq:
            return p if sp < sq else q
        return p if self.order_rank[p] > self.order_rank[q] else q

    def solve_cluster(self, st: State, members: set, stats: dict) -> State:
        while not self.out_of_budget():
            cs = self.conflicts(st, members)
            if not cs:
                return st
            p, q, kind = self.pick(cs)
            stats["steps"] += 1
            kids = []
            for a, b, tag in ((p, q, "ga"), (q, p, "na")):
                k = self.child(st, a, b)
                if k is not None and (p, q, kind) in self.conflicts(k, {p, q}):
                    # 다시 깔았는데 고른 충돌이 그대로면 그 자식은 버린다 (라우터 ↔ 충돌 판정 불일치 — 기록)
                    self.log.setdefault("unresolved_child", []).append([p, q, kind, tag])
                    k = None
                if k is not None:
                    kids.append((sum(rcost(r) for r in k.routes.values()), 0 if tag == "ga" else 1, tag, k))
            if kids:
                kids.sort(key=lambda x: (x[0], x[1]))
                cost, _, tag, nxt = kids[0]
                self.res.events.append(f"step {stats['steps']}: ({p},{q}) kind{kind} → {tag} children={len(kids)} cost={cost:.1f}")
                stats["children_alive"].append(len(kids))
                st = nxt
            else:
                x = self.drop_one(p, q)
                routes = dict(st.routes)
                routes.pop(x)
                failed = dict(st.failed)
                failed[x] = "coexist"
                prec = {(a, b) for a, b in st.prec if x not in (a, b)}
                self.res.events.append(f"step {stats['steps']}: ({p},{q}) 함께 놓을 수 없음 → {x} 실패")
                stats["dropped"].append(x)
                st = State(routes, prec, failed)
        return st

    def clusters(self, cs) -> list:
        adj = {}
        for a, b, _ in cs:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        seen, out = set(), []
        for v in sorted(adj):
            if v in seen:
                continue
            comp, stack = set(), [v]
            seen.add(v)
            while stack:
                u = stack.pop()
                comp.add(u)
                for w in adj[u]:
                    if w not in seen:
                        seen.add(w)
                        stack.append(w)
            out.append(comp)
        out.sort(key=lambda c: (-max(size_mm(self.pipes[x]) for x in c), min(c)))
        return out

    def run(self, root: dict) -> State:
        """root = {pid: RouteResult} (단독 경로 · 실패 포함), excluded = 분모 제외 id."""
        routes = {pid: r for pid, r in root["routes"].items() if r.status == "ok"}
        failed = {pid: f"solo:{r.status}" for pid, r in root["routes"].items() if r.status != "ok"}
        for pid in root["excluded"]:
            failed[pid] = "excluded"
        st = State(routes, set(), failed)
        rounds = []
        while not self.out_of_budget():
            cs = self.conflicts(st)
            if not cs:
                break
            cl = self.clusters(cs)
            rnd = {"conflicts": len(cs), "clusters": [len(c) for c in cl], "steps": 0, "dropped": [],
                   "children_alive": []}
            for c in cl:
                if self.out_of_budget():
                    break
                st = self.solve_cluster(st, c, rnd)
            rounds.append(rnd)
        self.log["rounds"] = rounds
        self.log["new_conflicts_after_recheck"] = [r["conflicts"] for r in rounds[1:]]
        self.log["first_solution"] = not self.exhausted and not self.conflicts(st)
        return st


def make_planner(budget_sec=None, root_fn=None, log=None):
    """pipeline.run(planner=...) 슬롯. root_fn(sc, router, time_limit, res) → {"routes", "excluded", "sec"} 를 바꿔 끼울 수
    있다 (작업자 2 루트). 기본 = 같은 프로세스 순차 루트."""
    log = log if log is not None else {}

    def planner(sc, router, time_limit):
        S = Search(sc, router, time_limit, budget_sec, log)
        t = time.perf_counter()
        if root_fn is None:
            req, exc = required_pipe_ids(sc)
            routes = {}
            for pid in sorted(req):
                routes[pid] = _route(S.res, sc, S.pipes[pid], router, time_limit, [])
            root = {"routes": routes, "excluded": sorted(exc)}
        else:
            root = root_fn(sc, router, time_limit, S.res)
        log["root_sec"] = time.perf_counter() - t
        log["root_status"] = {pid: r.status for pid, r in sorted(root["routes"].items())}
        st = S.run(root)
        log["search_sec"] = time.perf_counter() - t - log["root_sec"]
        log["reroutes"] = S.reroutes
        log["prec"] = sorted(st.prec)
        log["failed"] = dict(sorted(st.failed.items()))
        log["budget_exhausted"] = S.exhausted
        res = S.res
        for p in sc.pipes:
            res.routes[p.id] = st.routes.get(p.id) or (
                root["routes"][p.id] if p.id in root["routes"] and root["routes"][p.id].status != "ok"
                else RouteResult(p.id, "unreachable"))
        res.order = S.topo(st.prec, list(st.routes)) + sorted(st.failed)
        res.placement = list(res.order)
        res.total_sec = time.perf_counter() - S.t0
        return res

    planner.log = log
    return planner
