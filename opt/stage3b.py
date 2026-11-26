"""OPT.Stage3 (2026-11-25 점검 반영판) — `opt.stage3.Search3` 위에 더한 것.

① OPT.Dec28: 첫 해 뒤에는 되돌아가지 않는다. OPT.Dec22 재시도를 한 번 한 다음 멈춘다 (남은 예산은 LNS = OPT.Stage4 몫).
   되돌아가기 (§7.4 ⑥) 는 측정 비교용 스위치 `backtrack` 로만 남기고 기본값은 끈다.
② coord_record/1 (OPT.Dec26 ①): 기록 실행 (시드 31M · OPT_COORD_RECORD_DIR) 에서만 양보 판단 기록을 남긴다.
   두 자식의 바로 결과에 증분 검증 (필수 통과 · 서포트 실패 · J) 을 기록 실행에서만 계산한다. ε = 0 (OPT.Dec31).
   기록 계산은 탐색 결정에 쓰이지 않는다 — 기록을 켜도 끄도 경로가 같다 (시험으로 확인).
③ 최선 해가 바뀔 때마다 on_best 콜백을 부른다 — D139 정시 멈춤 (opt.deadline) 이 마지막 최선 해를 받는다.
④ 확장 수 예산 (OPT.Req7): 기록 실행은 벽시계가 아니라 누적 확장 수로 멈출 수 있다.

`opt/stage3.py` 는 2026-11-24 시작한 측정 대기열이 쓰는 판이라 고치지 않는다.
"""
import time

from . import _sk  # noqa: F401
from .priority import State, rcost
from .stage3 import Search3, better
from pipe_routing.pipeline import j_breakdown  # noqa: E402
from pipe_routing.verifier import PipeRoute, verify  # noqa: E402
from pipe_routing.verifier.core import centerlines_of  # noqa: E402
from pipe_routing.verifier.geom import segment_segment_distance  # noqa: E402
from pipe_routing.verifier.session import _segs  # noqa: E402

KIND_NAME = {1: "pipe_interference", 2: "terminal_reservation", 3: "support"}


class Search3b(Search3):
    def __init__(self, *a, backtrack=False, recorder=None, on_best=None, expand_budget=None, **kw):
        super().__init__(*a, **kw)
        self.backtrack = backtrack
        self.rec = recorder
        self.on_best = on_best
        self.expand_budget = expand_budget
        self.node = 0
        self.dive_parent = None
        self.depth = 0
        self.root_len = {}
        self.yielded = {}
        self.rerouted_n = {}

    def out_of_budget(self) -> bool:
        if self.expand_budget is not None and self.expanded >= self.expand_budget:
            self.exhausted = True
            return True
        return super().out_of_budget()

    def reroute(self, st_routes, prec, pid, why):
        self.rerouted_n[pid] = self.rerouted_n.get(pid, 0) + 1
        return super().reroute(st_routes, prec, pid, why)

    # ------------------------------------------------------------------ 기록용 계산 (탐색 결정에 쓰지 않음)
    @staticmethod
    def path_len(w):
        return sum(sum((a - b) ** 2 for a, b in zip(u, v)) ** 0.5 for u, v in zip(w, w[1:])) if w else None

    def pipe_state(self, pid, st):
        r = st.routes.get(pid)
        w = r.waypoints if r is not None else []
        L = self.path_len(w)
        L0 = self.root_len.get(pid)
        return {"ref": self.rec.ref.get(pid) if self.rec else None,
                "path_mm": round(L, 1) if L else None, "bends": max(0, len(w) - 2) if w else None,
                "length_increase_vs_root": round(L / L0 - 1, 4) if (L and L0) else None,
                "support_est": round(float(r.J_support_est or 0.0), 3) if r is not None else None,
                "n_above": len(self.ancestors(st.prec, pid)), "n_below": len(self.descendants(st.prec, pid)),
                "yielded_count": self.yielded.get(pid, 0), "rerouted_count": self.rerouted_n.get(pid, 0)}

    def min_clearance(self, st, p, q):
        if p not in st.routes or q not in st.routes:
            return None
        sp = _segs(centerlines_of(self.pipes[p], PipeRoute(p, st.routes[p].waypoints)))
        sq = _segs(centerlines_of(self.pipes[q], PipeRoute(q, st.routes[q].waypoints)))
        d, _, _ = segment_segment_distance(sp[0][:, None], sp[1][:, None], sq[0][None], sq[1][None])
        return round(float((d - sp[2][:, None] - sq[2][None]).min()) - self.pipes[p].radius - self.pipes[q].radius, 1)

    def quick_verify(self, st):
        """증분 검증 결과 (기록 실행에서만): 필수 통과 수 · support 위반 수 · J (통과 배관 합)."""
        ids = sorted(st.routes)
        rep = verify(self.sc, [PipeRoute(a, st.routes[a].waypoints) for a in ids], std=self.std)["pipes"]
        passed = {a for a in ids if rep[a].success}
        J = sum(j_breakdown(self.pipes[a], st.routes[a].waypoints, rep[a].fittings, rep[a].supports, self.sc)["total"]
                for a in passed)
        return {"required_passed": len(self.required & passed),
                "support_violations": sum(1 for a in ids if rep[a].layer0.get("support") is False),
                "J": round(J, 3)}

    # ------------------------------------------------------------------ 한 단계 (Search3.step 과 같은 결정 + 기록)
    def step(self, st, cs, stats):
        self.count_history(cs)
        p, q, kind = self.pick(cs)
        stats["steps"] += 1
        rec = None
        if self.rec is not None:
            before = {c[:2] for c in self.conflicts(st)}
            members = self.cluster_of(cs, p)
            rec = {"kind": "yield",
                   "id": {"node": self.node, "parent": self.dive_parent, "p_ref": self.rec.ref.get(p),
                          "q_ref": self.rec.ref.get(q)},
                   "meta": {"depth": self.depth, "expanded": self.expanded,
                            "sec": round(time.perf_counter() - self.t0, 3)},
                   "feat": {"budget": {"remaining_expanded": (round(1 - self.expanded / self.expand_budget, 4)
                                                              if self.expand_budget else None),
                                       "remaining_wall": (round(1 - (time.perf_counter() - self.t0) / self.budget, 4)
                                                          if self.budget else None)},
                            "conflict": {"kind": KIND_NAME[kind], "overlap_mm": None, "position": None},
                            "context": None,
                            "p": self.pipe_state(p, st), "q": self.pipe_state(q, st),
                            "relation": {"history": self.hist.get((p, q) if p < q else (q, p), 0),
                                         "min_clearance_mm": self.min_clearance(st, p, q)},
                            "cluster": {"size": len(members), "n_conflicts": len(cs)}},
                   "label": {}}
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
            if rec is not None:
                br = {"found": k is not None, "status": status, "n_rerouted": len(self.reroutes) - n0,
                      "expanded": self.expanded - e0, "sec": round(time.perf_counter() - t0, 3),
                      "router_cost_delta": (round(cost - sum(rcost(r) for r in st.routes.values()), 3)
                                            if cost is not None else None)}
                if k is not None:
                    after = {c[:2] for c in self.conflicts(k)}
                    br.update(remaining_conflicts=len(after), new_conflicts=len(after - before),
                              verify=self.quick_verify(k))
                rec["label"][f"immediate_{'p_first' if tag == 'ga' else 'q_first'}"] = br
            if k is not None:
                kids.append((cost, 0 if tag == "ga" else 1, tag, k))
        if rec is not None:
            self.rec.yield_row(rec)
        self.node += 1
        if kids:
            kids.sort(key=lambda x: (x[0], x[1]))
            cost, _, tag, nxt = kids[0]
            for c2, _, tag2, other in kids[1:]:
                self.stack.append((other, None, tag2))
            y = q if tag == "ga" else p
            self.yielded[y] = self.yielded.get(y, 0) + 1
            if rec is not None:
                rec["meta"]["choice"] = {"explored": "p_first" if tag == "ga" else "q_first", "prob": 1.0,
                                         "rule": "router_cost_sum_then_p_first",
                                         "compared": [k_[0] for k_ in kids]}
                self.cur_rows.append((rec, tag))
            self.dive_parent, self.depth = self.node - 1, self.depth + 1
            self.res.events.append(f"step {stats['steps']}: ({p},{q}) kind{kind} → {tag} children={len(kids)} cost={cost:.1f}")
            stats["children_alive"].append(len(kids))
            return nxt
        x = self.drop_one(p, q)
        routes = dict(st.routes)
        routes.pop(x)
        failed = dict(st.failed)
        failed[x] = "coexist"
        prec = {(a, b) for a, b in st.prec if x not in (a, b)}
        if rec is not None:
            rec["meta"]["choice"] = {"explored": "drop_" + ("p" if x == p else "q"), "prob": 1.0, "rule": "OPT.Dec7"}
        self.res.events.append(f"step {stats['steps']}: ({p},{q}) kind{kind} 함께 놓을 수 없음 → {x} 실패")
        stats["dropped"].append(x)
        return State(routes, prec, failed)

    def cluster_of(self, cs, p):
        adj = {}
        for a, b, _ in cs:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        seen, stack = {p}, [p]
        while stack:
            for w in adj.get(stack.pop(), ()):
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        return seen

    # ------------------------------------------------------------------ anytime (OPT.Dec28)
    def anytime(self, root) -> dict:
        routes = {pid: r for pid, r in root["routes"].items() if r.status == "ok"}
        failed = {pid: f"solo:{r.status}" for pid, r in root["routes"].items() if r.status != "ok"}
        for pid in root["excluded"]:
            failed[pid] = "excluded"
        self.root_len = {pid: self.path_len(r.waypoints) for pid, r in routes.items()}
        st = State(routes, set(), failed)
        self.expanded += sum(int(r.expanded or 0) for r in root["routes"].values())
        best, best_score, points, dives, first = None, None, [], [], None
        retried, last_tag, st_end = False, None, st
        while True:
            self.cur_rows = []
            st_end, score, passed, rounds = self.dive(st)
            dives.append({"rounds": rounds, "score": score, "origin": last_tag})
            if score is not None and self.rec is not None:
                for rec, tag in self.cur_rows:
                    rec["label"]["later"] = {"explored": "p_first" if tag == "ga" else "q_first",
                                             "candidate": list(score)}
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
                    if self.on_best:
                        self.on_best(best, best_score, pt)
                if not retried:                       # OPT.Dec22 — 첫 해 뒤 한 번
                    retried = True
                    self.extra = self.retry_timeouts(st_end)
                    if self.extra:
                        self.stack.append((st_end, None, "retry"))
            if self.out_of_budget():
                break
            if not self.backtrack:                    # OPT.Dec28 — 재시도 갈래만 풀고 멈춘다
                nxt = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][2] == "retry"), None)
                if nxt is None or last_tag == "retry":
                    break
                st0, _, tag = self.stack.pop(nxt)
            else:
                if not self.stack:
                    break
                st0, _, tag = self.stack.pop()
            st = self.inject_extra(st0)
            last_tag = tag
            self.dive_parent, self.depth = None, 0
        if best is None:
            best = st_end
        self.log["dives"] = dives
        self.log["stop"] = ("budget" if self.exhausted else
                            ("stack_empty" if self.backtrack else "first_and_retry"))
        self.log["backtrack"] = self.backtrack
        self.log["first"] = {"score": list(first[1]), "sec": round(first[2], 3), "expanded": first[3]} if first else None
        self.log["n_verify"] = self.n_verify
        self.log["stack_left"] = len(self.stack)
        return {"best": best, "best_score": best_score, "points": points, "first": first}
