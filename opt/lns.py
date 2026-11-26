"""OPT.Stage4a — 학습 없는 LNS (ALNS) (CLAUDE.md §7.9, docs/design/lns_baseline.md, OPT.Dec27 ~ Dec30).

첫 해 (모두 설치) + OPT.Dec22 재시도 뒤 남은 예산 동안 반복한다:
  후보 구역 만들기 (R0 ~ R4) → 가중치 룰렛으로 하나 고르기 → 걷어내기 → 나머지를 placed 로 두고 다시 깔기
  (부재 예약 켬 — 라우터 기본값, OPT.Dec13) → 충돌 1 · 2 · 3 검사 → 검증기 채점 → D77 로 좋아질 때만 받아들이기 (OPT.Dec29).
못 깔거나 충돌이 남으면 원래 경로 그대로 — 통과 수는 줄지 않는다.

값 (모두 lns_baseline.md 의 `추정:` 출발값 그대로 — 바꾸지 않는다, §10-4):
  k 출발 3 (밀집 4) · 개선 없이 20 회면 +1 · 최대 8 · 개선이 나오면 출발값으로
  근처 = 경로 상자 (축 정렬) 여유 r₁ + r₂ + 500 mm 가 겹치는 배관, 가까운 순 → id 순
  점수 새 최선 3 · 받아들임 1 · 거절 0, ÷ 그 반복의 확장 수 (백만 단위) · 10 회마다 w ← 0.7 w + 0.3 × 구간 평균 · 하한 0.05
  R3 는 가중치 0.05 고정 (OPT.Dec30) · 고치기 방법 2 가지 (큰 구경 먼저 · 시드 무작위 순서) 도 같은 방식의 가중치

이 문서에 숫자가 없어 OPT 가 정한 구현 선택 (보고서 §에 적는다 — 값이 아니라 방식):
  - 반복 "한 판" = 같은 해 S 에서 만든 구역 둘 (반복 번호 i, i+1). 작업자 2 개면 동시에, 1 개면 차례로 다시 깐다.
    받아들이기는 반복 번호 순 — i 를 먼저, 그 결과 위에서 i+1 을 다시 검사 (lns_baseline §7). 작업자 수와 무관하게
    같은 판 구조라 작업자 1 ↔ 2 결과가 같다 (OPT.Req6).
  - 두 번째 구역은 규칙을 룰렛 순서 (비복원) 로 하나씩 물어 첫 구역과 배관이 겹치지 않고 상자가 근처 여유 밖인 첫 후보.
    없으면 그 판은 구역 하나.
  - 규칙 안에서 후보 고르기: R0 = 실패 배관 균등 무작위 · R1 = 놓인 배관 균등 무작위 · R2 = (라우터 비용 지금 − 루트) > 0
    에 비례 · R3 = 높이 차로 못 묶인 쌍 균등 무작위 · R4 = displaced 막힘 줄 수에 비례.
  - 무작위 LNS (비교 (가)) = R1 만, 고치기 방법도 균등 무작위, 가중치 갱신 없음.
  - 난수 = random.Random((시나리오 시드 × 1,000,003 + 반복 번호)) — 반복마다 새로 (lns_baseline §7).
"""
import dataclasses
import multiprocessing as mp
import random
import time

from . import _sk  # noqa: F401
from .priority import rcost, size_mm
from .stage3 import better
from pipe_routing.multi import routes_conflict  # noqa: E402
from pipe_routing.pipeline import j_breakdown  # noqa: E402
from pipe_routing.router_astar import RouteResult  # noqa: E402
from pipe_routing.verifier import PipeRoute, verify  # noqa: E402
from pipe_routing.verifier.hint import SupportHint  # noqa: E402

RULES = ("R0", "R1", "R2", "R3", "R4")
REPAIRS = ("size_desc", "random")
K_MAX = 8
K_STALL = 20
NEAR_MM = 500.0
W_FLOOR = 0.05
R3_FIXED = 0.05
UPDATE_EVERY = 10
DECAY = 0.7
PSI = {"best": 3.0, "accept": 1.0, "reject": 0.0}
HEIGHT_REASON = "지지면 쪽 표면 높이 차"


def k_start(set_name):
    return 4 if set_name in ("dense30", "dense50") else 3


def _strip(r):
    d = dataclasses.asdict(r)
    d.pop("states", None)
    return RouteResult(**d)


# ---------------------------------------------------------------------- 채점 (공개 함수만 — OPT.Req13 · D128)
def evaluate(sc, pipes, std, required, wps: dict):
    """검증기 채점 + 충돌 3 쌍. 반환 dict (score, passed, J_by_pipe, c3, support_viol, sec)."""
    t = time.perf_counter()
    ids = sorted(wps)
    rep = verify(sc, [PipeRoute(a, wps[a]) for a in ids], std=std)["pipes"]
    passed = {a for a in ids if rep[a].success}
    jb = {a: j_breakdown(pipes[a], wps[a], rep[a].fittings, rep[a].supports, sc) for a in passed}
    J = sum(v["total"] for v in jb.values())
    full = int(all(a in passed for a in required))
    viol = [a for a in ids if rep[a].layer0.get("support") is False]
    c3 = []
    if viol:
        h = SupportHint(sc, {a: wps[a] for a in ids}, std=std)
        for p in viol:
            solo = verify(sc, [PipeRoute(p, wps[p])], std=std)["pipes"][p]
            if solo.layer0.get("support") is False:
                continue
            for q in sorted({r["blocker"] for r in h.support_blockers(p) if r["kind"] == "gap"}):
                c3.append((p, q, 3))
    return {"score": (full, len(passed), round(J, 6)), "passed": passed, "jb": jb, "rep": rep, "c3": c3,
            "support_viol": len(viol), "sec": time.perf_counter() - t}


class LNS:
    def __init__(self, S, st, score, *, set_name, seed, mode="alns", workers=2, root_routes=None,
                 on_best=None, on_progress=None, expand_budget=None, t0=None):
        """S = Search3b (sc · pipes · router · std · required · hist · expanded 를 빌려 쓴다). st = 첫 해 State."""
        self.S, self.sc, self.pipes, self.std = S, S.sc, S.pipes, S.std
        self.router, self.tl = S.router, 60.0
        self.required = S.required
        self.mode, self.workers, self.set_name = mode, workers, set_name
        self.seed = int(seed or 0)
        self.routes = dict(st.routes)            # pid → RouteResult
        self.failed = dict(st.failed)
        self.score = score
        self.root = {pid: r for pid, r in (root_routes or {}).items() if r.status == "ok"}
        self.on_best, self.on_progress = on_best, on_progress
        self.expand_budget = expand_budget
        self.t0 = t0 if t0 is not None else time.perf_counter()
        self.k0 = k_start(set_name)
        self.k = self.k0
        self.stall = 0
        self.it = 0
        rules = ("R1",) if mode == "random" else RULES
        self.rules = rules
        self.w = {r: (R3_FIXED if r == "R3" else 1.0) for r in rules}
        self.wr = {m: 1.0 for m in REPAIRS}
        self.acc = {r: [0.0, 0] for r in rules}       # 갱신 구간 (점수 합, 사용 수)
        self.accr = {m: [0.0, 0] for m in REPAIRS}
        self.stats = {r: {"used": 0, "accepted": 0, "best": 0, "dJ": 0.0, "dpassed": 0, "expanded": 0, "no_candidate": 0,
                          "fail_route": 0, "fail_conflict": 0, "fail_c3": 0, "not_better": 0} for r in RULES}
        self.rstats = {m: {"used": 0, "accepted": 0} for m in REPAIRS}
        self.revived = []                 # R0 로 되살린 배관
        self.iters = []                   # 반복 기록 (시간 분포 · k 변화)
        self.k_hist = [(0, self.k)]
        self.cache = {}                   # 해마다 다시 만드는 값 (R3 쌍 · R4 막힘 · 상자)
        self.points = []
        self.ver = 0                      # 받아들인 해 번호 (캐시 열쇠)
        self.cur_eval = None

    # ------------------------------------------------------------------ 상자 · 근처
    def box(self, pid, w=None):
        w = w if w is not None else self.routes[pid].waypoints
        xs = list(zip(*w))
        r = self.pipes[pid].radius
        return [min(v) - r for v in xs], [max(v) + r for v in xs]

    @staticmethod
    def gap(b1, b2):
        g = 0.0
        for a0, a1, c0, c1 in zip(b1[0], b1[1], b2[0], b2[1]):
            d = max(c0 - a1, a0 - c1, 0.0)
            g += d * d
        return g ** 0.5

    def near(self, pid, w=None, exclude=()):
        b = self.box(pid, w)
        out = []
        for q in sorted(self.routes):
            if q == pid or q in exclude:
                continue
            g = self.gap(b, self.box(q))
            if g <= NEAR_MM:
                out.append((g, q))
        return [q for _, q in sorted(out)]

    def fill(self, region, anchor, anchor_w=None, k=None):
        k = k if k is not None else self.k
        out = list(dict.fromkeys(region))
        for q in self.near(anchor, anchor_w, exclude=set(out)):
            if len(out) >= k:
                break
            out.append(q)
        return out[:K_MAX]

    # ------------------------------------------------------------------ 규칙 (지금 해 → 후보 하나)
    def cand(self, rule, rng):
        f = getattr(self, "rule_" + rule)
        return f(rng)

    def rule_R0(self, rng):
        fs = sorted(p for p, why in self.failed.items() if why == "coexist" and p in self.root)
        if not fs:
            return None
        f = fs[rng.randrange(len(fs))]
        w = self.root[f].waypoints
        blk = [q for q in sorted(self.routes) if routes_conflict(self.pipes[f], w, self.pipes[q], self.routes[q].waypoints)]
        blk.sort(key=lambda q: (-size_mm(self.pipes[q]), q))
        region = [f] + blk[:K_MAX - 1]
        if len(region) < self.k:
            region = self.fill(region, f, w)
        return {"rule": "R0", "pipes": region, "first": f}

    def rule_R1(self, rng):
        ids = sorted(self.routes)
        if not ids:
            return None
        p = ids[rng.randrange(len(ids))]
        return {"rule": "R1", "pipes": self.fill([p], p)}

    def rule_R2(self, rng):
        d = []
        for p in sorted(self.routes):
            if p in self.root:
                x = rcost(self.routes[p]) - rcost(self.root[p])
                if x > 1e-6:
                    d.append((p, x))
        if not d:
            return None
        p = self.weighted(rng, d)
        hist = self.S.hist
        part = sorted(((hist.get((p, q) if p < q else (q, p), 0), q) for q in self.routes if q != p), key=lambda t: (-t[0], t[1]))
        region = [p] + ([part[0][1]] if part and part[0][0] > 0 else [])
        return {"rule": "R2", "pipes": self.fill(region, p)}

    def rule_R3(self, rng):
        pairs = self.rack_pairs()
        if not pairs:
            return None
        a, b = pairs[rng.randrange(len(pairs))]
        ba, bb = self.box(a), self.box(b)
        span = ([min(x, y) for x, y in zip(ba[0], bb[0])], [max(x, y) for x, y in zip(ba[1], bb[1])])
        mid = [q for q in sorted(self.routes) if q not in (a, b) and self.gap(span, self.box(q)) <= 0.0]
        region = ([a, b] + mid)[:K_MAX]
        return {"rule": "R3", "pipes": self.fill(region, a) if len(region) < self.k else region}

    def rule_R4(self, rng):
        disp = self.displaced()
        if not disp:
            return None
        p = self.weighted(rng, [(p, len(rows)) for p, rows in sorted(disp.items())])
        qs = sorted({q for q in disp[p]})
        return {"rule": "R4", "pipes": self.fill(([p] + qs)[:K_MAX], p)}

    @staticmethod
    def weighted(rng, items):
        tot = sum(x for _, x in items)
        u = rng.random() * tot
        for p, x in items:
            u -= x
            if u <= 0:
                return p
        return items[-1][0]

    # ------------------------------------------------------------------ 해마다 캐시하는 값 (R3 · R4)
    def hint(self):
        key = ("hint", self.ver)
        if key not in self.cache:
            self.cache = {k: v for k, v in self.cache.items() if k[1] == self.ver}
            self.cache[key] = SupportHint(self.sc, {a: self.routes[a].waypoints for a in sorted(self.routes)}, std=self.std)
        return self.cache[key]

    def rack_pairs(self):
        key = ("rack", self.ver)
        if key in self.cache:
            return self.cache[key]
        from pipe_routing.verifier import support_group as sg
        rep = self.cur_eval["rep"]
        h = self.hint()
        cs = [(pid, s) for pid in sorted(self.routes) for s in rep[pid].supports if s["form"] == "C" and not s.get("origin")]
        lim = sg.l1_max(self.std)
        out = []
        for i, (pa, sa) in enumerate(cs):
            ua = sa["axis"]
            if abs(ua[2]) > 1e-9 or max(abs(v) for v in ua) < 1 - 1e-9:
                continue
            ax = max(range(3), key=lambda k: abs(ua[k]))
            for pb, sb in cs[i + 1:]:
                if pb == pa or sb["face"] != sa["face"] or sb["axis"] != ua or \
                        abs(sa["pos"][ax] - sb["pos"][ax]) > sg.WINDOW_MM or abs(sa["pos"][1 - ax] - sb["pos"][1 - ax]) > lim:
                    continue
                g = h.groupable((pa, sa["pos"], sa["axis"]), (pb, sb["pos"], sb["axis"]), face=sa["face"])
                if not g["ok"] and str(g.get("reason") or "").startswith(HEIGHT_REASON):
                    out.append(tuple(sorted((pa, pb))))
        out = sorted(set(out))
        self.cache[key] = out
        return out

    def displaced(self):
        key = ("disp", self.ver)
        if key in self.cache:
            return self.cache[key]
        h = self.hint()
        out = {}
        for p in sorted(self.routes):
            rows = [r["blocker"] for r in h.support_blockers(p) if r["kind"] == "displaced"]
            if rows:
                out[p] = rows
        self.cache[key] = out
        return out

    # ------------------------------------------------------------------ 다시 깔기 (작업자에서 돈다)
    def repair(self, cand, method, rng):
        t = time.perf_counter()
        region = cand["pipes"]
        keep = {a: self.routes[a] for a in self.routes if a not in region}
        first = cand.get("first")
        rest = [p for p in region if p != first]
        if method == "size_desc":
            rest.sort(key=lambda p: (-size_mm(self.pipes[p]), p))
        else:
            rest = sorted(rest)
            rng.shuffle(rest)
        order = ([first] if first else []) + rest
        new, exp, calls = {}, 0, []
        for p in order:
            placed = [(self.pipes[a], keep[a].waypoints) for a in sorted(keep)] + \
                     [(self.pipes[a], new[a].waypoints) for a in order if a in new]
            t1 = time.perf_counter()
            r = self.router(self.sc, self.pipes[p], self.tl, placed)
            exp += int(r.expanded or 0)
            calls.append({"pipe": p, "status": r.status, "sec": round(time.perf_counter() - t1, 3),
                          "expanded": int(r.expanded or 0)})
            if r.status != "ok":
                return {"ok": False, "why": "route", "expanded": exp, "calls": calls,
                        "route_sec": time.perf_counter() - t, "eval_sec": 0.0}
            new[p] = _strip(r)
        route_sec = time.perf_counter() - t
        bad = self.conflicts_with(new, keep)
        if bad:
            return {"ok": False, "why": "conflict", "expanded": exp, "calls": calls, "route_sec": route_sec,
                    "eval_sec": 0.0, "conflicts": bad}
        wps = {a: r.waypoints for a, r in keep.items()}
        wps.update({a: r.waypoints for a, r in new.items()})
        ev = evaluate(self.sc, self.pipes, self.std, self.required, wps)
        return {"ok": True, "new": new, "expanded": exp, "calls": calls, "route_sec": route_sec, "eval_sec": ev["sec"],
                "score": ev["score"], "c3": ev["c3"], "eval": {k: ev[k] for k in ("passed", "jb", "rep", "support_viol")}}

    def conflicts_with(self, new, keep):
        out = []
        ids = sorted(new)
        for i, a in enumerate(ids):
            for q in sorted(keep):
                if routes_conflict(self.pipes[a], new[a].waypoints, self.pipes[q], keep[q].waypoints):
                    out.append((a, q))
            for b in ids[i + 1:]:
                if routes_conflict(self.pipes[a], new[a].waypoints, self.pipes[b], new[b].waypoints):
                    out.append((a, b))
        return out

    # ------------------------------------------------------------------ 한 판
    def rng(self, it):
        return random.Random(self.seed * 1_000_003 + it)

    def draw(self, rng, w, exclude=()):
        items = [(r, w[r]) for r in sorted(w) if r not in exclude]
        return self.weighted(rng, items) if items else None

    def choose(self, it, taken=None):
        """반복 it 의 (후보, 고치기 방법, rng). taken = 첫 구역 (두 번째 구역이면 겹침 금지)."""
        rng = self.rng(it)
        tried, rules_try = set(), []
        while True:
            if self.mode == "random":
                r = "R1" if "R1" not in tried else None
            else:
                r = self.draw(rng, self.w, tried)
            if r is None:
                return None
            tried.add(r)
            c = self.cand(r, rng)
            if c is None:
                rules_try.append(r)
                continue
            if taken is not None and not self.apart(c["pipes"], taken):
                if self.mode == "random":
                    return None
                continue
            m = (REPAIRS[rng.randrange(2)] if self.mode == "random" else self.draw(rng, self.wr))
            c["no_candidate"] = rules_try
            return c, m, rng

    def apart(self, a, b):
        if set(a) & set(b):
            return False
        boxes = []
        for p in b:
            w = self.routes[p].waypoints if p in self.routes else self.root[p].waypoints
            boxes.append(self.box(p, w))
        for p in a:
            w = self.routes[p].waypoints if p in self.routes else self.root[p].waypoints
            bp = self.box(p, w)
            if any(self.gap(bp, bq) <= NEAR_MM for bq in boxes):
                return False
        return True

    def run_jobs(self, jobs):
        """jobs = [(cand, method, rng)] → 결과 목록 (순서 그대로). 작업자 2 면 두 번째를 자식 프로세스에서."""
        if self.workers <= 1 or len(jobs) < 2:
            return [self.repair(*j) for j in jobs]
        ctx = mp.get_context("fork")
        rd, wr = ctx.Pipe(duplex=False)

        def child():
            rd.close()
            try:
                wr.send(self.repair(*jobs[1]))
            except Exception as e:  # noqa: BLE001
                import traceback
                wr.send({"ok": False, "why": "error", "error": traceback.format_exc() + repr(e), "expanded": 0,
                         "calls": [], "route_sec": 0.0, "eval_sec": 0.0})
            wr.close()
        pr = ctx.Process(target=child)
        pr.start()
        wr.close()
        a = self.repair(*jobs[0])
        b = rd.recv()
        pr.join()
        return [a, b]

    def accept(self, res, rule, it, kind):
        self.routes.update(res["new"])
        for p in res["new"]:
            if p in self.failed:
                self.failed.pop(p)
                if rule == "R0":
                    self.revived.append(p)
        old = self.score
        self.score = res["score"]
        self.ver += 1
        self.cur_eval = res["eval"]
        st = self.stats[rule]
        st["dJ"] += self.score[2] - old[2] if self.score[1] == old[1] else 0.0
        st["dpassed"] += self.score[1] - old[1]
        pt = {"routed": len(self.routes), "success": self.score[1],
              "success_rate": round(self.score[1] / len(self.sc.pipes), 4), "J_total": round(self.score[2], 3),
              "full_success": self.score[0], "required_passed": len(self.required & res["eval"]["passed"]),
              "expanded": self.S.expanded, "sec": round(time.perf_counter() - self.t0, 3), "move": f"lns:{rule}",
              "iteration": it}
        self.points.append(pt)
        if self.on_best:
            from .priority import State
            self.on_best(State(dict(self.routes), set(), dict(self.failed)), self.score, pt)

    def recheck(self, res):
        """첫 구역을 받아들인 뒤 두 번째 구역을 새 해 위에서 다시 검사 · 채점."""
        new = res["new"]
        keep = {a: self.routes[a] for a in self.routes if a not in new}
        t = time.perf_counter()
        bad = self.conflicts_with(new, keep)
        if bad:
            return dict(res, ok=False, why="conflict_after_merge", eval_sec=res["eval_sec"])
        wps = {a: r.waypoints for a, r in keep.items()}
        wps.update({a: r.waypoints for a, r in new.items()})
        ev = evaluate(self.sc, self.pipes, self.std, self.required, wps)
        return dict(res, score=ev["score"], c3=ev["c3"], eval={k: ev[k] for k in ("passed", "jb", "rep", "support_viol")},
                    eval_sec=res["eval_sec"] + time.perf_counter() - t, rechecked=True)

    def one_round(self):
        it = self.it
        a = self.choose(it)
        if a is None:
            return False
        jobs = [a]
        b = self.choose(it + 1, taken=a[0]["pipes"])
        if b is not None:
            jobs.append(b)
        t = time.perf_counter()
        res = self.run_jobs(jobs)
        wall = time.perf_counter() - t
        accepted_first = False
        for j, ((cand, method, _), r) in enumerate(zip(jobs, res)):
            n = it + j
            rule = cand["rule"]
            if j == 1 and accepted_first and r["ok"]:
                r = self.recheck(r)
            self.S.expanded += r["expanded"]
            st = self.stats[rule]
            st["used"] += 1
            st["expanded"] += r["expanded"]
            for nr in cand.get("no_candidate", []):
                self.stats[nr]["no_candidate"] += 1
            self.rstats[method]["used"] += 1
            outcome = "reject"
            if not r["ok"]:
                st["fail_route" if r["why"] == "route" else "fail_conflict"] += 1
            elif r["c3"]:
                st["fail_c3"] += 1
            elif better(r["score"], self.score):
                outcome = "best"
                st["accepted"] += 1
                st["best"] += 1
                self.rstats[method]["accepted"] += 1
                self.accept(r, rule, n, outcome)
                accepted_first = accepted_first or j == 0
            else:
                st["not_better"] += 1
            psi = PSI[outcome] / max(r["expanded"], 1) * 1e6
            self.acc[rule][0] += psi
            self.acc[rule][1] += 1
            self.accr[method][0] += psi
            self.accr[method][1] += 1
            self.iters.append({"it": n, "rule": rule, "method": method, "k": self.k, "n_pipes": len(cand["pipes"]),
                               "outcome": outcome, "why": r.get("why"), "expanded": r["expanded"],
                               "route_sec": round(r["route_sec"], 3), "eval_sec": round(r["eval_sec"], 3),
                               "n_route": len(r["calls"]), "round_wall": round(wall, 3), "pair": len(jobs),
                               "sec": round(time.perf_counter() - self.t0, 3)})
            self.after_iter(n, outcome)
        self.it += len(jobs)
        return True

    def after_iter(self, n, outcome):
        if outcome == "best":
            self.stall = 0
            if self.k != self.k0:
                self.k = self.k0
                self.k_hist.append((n + 1, self.k))
        else:
            self.stall += 1
            if self.stall >= K_STALL and self.k < K_MAX:
                self.k += 1
                self.stall = 0
                self.k_hist.append((n + 1, self.k))
        if self.mode != "random" and (n + 1) % UPDATE_EVERY == 0:
            for d, acc in ((self.w, self.acc), (self.wr, self.accr)):
                for r, (s, u) in acc.items():
                    if u and not (d is self.w and r == "R3"):
                        d[r] = max(W_FLOOR, DECAY * d[r] + (1 - DECAY) * s / u)
                    acc[r] = [0.0, 0]

    def progress(self):
        return {"iterations": self.it, "k": self.k, "k_hist": self.k_hist, "weights": dict(self.w),
                "repair_weights": dict(self.wr), "rules": self.stats, "repairs": self.rstats,
                "revived": list(self.revived), "iters": self.iters, "points": self.points, "score": list(self.score),
                "expanded": self.S.expanded, "mode": self.mode, "workers": self.workers}

    def run(self):
        if self.cur_eval is None:
            ev = evaluate(self.sc, self.pipes, self.std, self.required, {a: r.waypoints for a, r in self.routes.items()})
            self.cur_eval = {k: ev[k] for k in ("passed", "jb", "rep", "support_viol")}
            self.score = ev["score"]
        while True:
            if self.expand_budget is not None and self.S.expanded >= self.expand_budget:
                break
            if not self.one_round():
                break
            if self.on_progress:
                self.on_progress(self.progress())
        return self.progress()
