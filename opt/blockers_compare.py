"""OPT.Req13 · D128 이전 대조 — 서포트 충돌 책임 쌍: 옛 비공개 함수 판정 ↔ skeleton 공개 조회 `support_blockers`.

옛 판정 (OPT.Stage1 `opt/conflicts.py` · OPT.Stage3 `Search3.score_and_support_conflicts`):
  p = 함께 검증에서 support 위반 · 단독 검증에서 support 통과. q = p 근처 (영향 범위 안) 배관 중 그 하나만 빼면
  p 가 통과하는 배관 (단일), 없으면 빼서 불가 구간이 줄어드는 배관 (복수).
새 판정: 같은 p 에 대해 `SupportHint(sc, routes).support_blockers(p)` 중 kind = "gap" (support 위반 구간 안) 의 blocker 집합.
  kind = "displaced" (위반 없이 다른 위치 · 형식으로 옮겨짐) 는 OPT.Dec12 로 충돌이 아니므로 따로 센다.
대상 배치: 검증용 40 의 ① OPT.Stage1 루트 (작업자 2) ② OPT.Stage3 기준선 anytime 예산 끝 해 ③ OPT.Stage3 OPT (Dec28) 해.
사용: python -m opt.blockers_compare -o out/blockers_compare.json
"""
import argparse
import json
import time
from pathlib import Path

from . import _sk  # noqa: F401
from pipe_routing.scenario import load  # noqa: E402
from pipe_routing.support_diag import _support_of  # noqa: E402
from pipe_routing.support_std import load_standard  # noqa: E402
from pipe_routing.verifier import PipeRoute, verify  # noqa: E402
from pipe_routing.verifier.core import centerlines_of  # noqa: E402
from pipe_routing.verifier.geom import segment_segment_distance  # noqa: E402
from pipe_routing.verifier.hint import SupportHint  # noqa: E402
from pipe_routing.verifier.session import _reach, _segs  # noqa: E402


def old_pairs(sc, pipes, wps, std, reach, targets):
    ids = sorted(wps)
    routes = {a: PipeRoute(a, wps[a]) for a in ids}
    cls = {a: centerlines_of(pipes[a], routes[a]) for a in ids}
    segs = {a: _segs(c) for a, c in cls.items()}
    out = {}
    for p in targets:
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
        out[p] = {"blockers": sorted(single or reducing), "kind": "single" if single else ("multiple" if reducing else "none")}
    return out


def new_pairs(sc, wps, std, targets):
    h = SupportHint(sc, {a: wps[a] for a in sorted(wps)}, std=std)
    out = {}
    for p in targets:
        rows = h.support_blockers(p)
        out[p] = {"gap": sorted({r["blocker"] for r in rows if r["kind"] == "gap"}),
                  "displaced": sorted({r["blocker"] for r in rows if r["kind"] == "displaced"})}
    return out


def one(sc, wps, std, reach):
    pipes = {p.id: p for p in sc.pipes}
    ids = sorted(wps)
    rep = verify(sc, [PipeRoute(a, wps[a]) for a in ids], std=std)["pipes"]
    viol = [a for a in ids if rep[a].layer0.get("support") is False]
    targets = []
    for a in viol:
        solo = verify(sc, [PipeRoute(a, wps[a])], std=std)["pipes"][a]
        if solo.layer0.get("support") is not False:
            targets.append(a)
    o = old_pairs(sc, pipes, wps, std, reach, targets)
    n = new_pairs(sc, wps, std, targets)
    rows = []
    for p in targets:
        rows.append({"pipe": p, "old": o[p]["blockers"], "old_kind": o[p]["kind"], "new_gap": n[p]["gap"],
                     "new_displaced": n[p]["displaced"], "same": o[p]["blockers"] == n[p]["gap"]})
    return {"support_violation": len(viol), "targets": len(targets), "rows": rows}


def states(val: Path):
    man = json.loads((val / "manifest.json").read_text())
    for s, rows in man["sets"].items():
        for r in rows:
            stem = Path(r["file"]).stem
            path = val / r["file"]
            f = Path("out/stage1/w2") / f"{stem}_root.json"
            if f.exists():
                d = json.loads(f.read_text())
                yield stem, str(path), "stage1_root", {x["pipe_id"]: x["waypoints"] for x in d["pipes"]
                                                         if x["status"] == "ok"}
            for tag, f in (("stage3_baseline", Path("out/stage3/baseline") / f"{stem}_output.json"),
                           ("stage3_opt", Path("out/stage3b/opt") / f"{stem}_output.json")):
                if f.exists():
                    d = json.loads(f.read_text())
                    yield stem, str(path), tag, {x["pipe_id"]: x["waypoints"] for x in d["routes"] if x["waypoints"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--val", default="out/val")
    ap.add_argument("-o", "--out", default="out/blockers_compare.json")
    a = ap.parse_args(argv)
    std = load_standard()
    reach = _reach(std)
    res = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
    for stem, path, tag, wps in states(Path(a.val)):
        key = f"{tag}/{stem}"
        if key in res:
            continue
        t = time.perf_counter()
        r = one(load(path), wps, std, reach)
        r["sec"] = round(time.perf_counter() - t, 1)
        res[key] = r
        print(key, r["support_violation"], r["targets"], sum(x["same"] for x in r["rows"]), r["sec"], flush=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    tot = {"targets": 0, "same": 0, "diff": []}
    for k, r in res.items():
        tot["targets"] += r["targets"]
        tot["same"] += sum(x["same"] for x in r["rows"])
        tot["diff"] += [dict(x, state=k) for x in r["rows"] if not x["same"]]
    print(json.dumps({"targets": tot["targets"], "same": tot["same"], "n_diff": len(tot["diff"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
