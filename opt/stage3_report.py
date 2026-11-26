"""OPT.Stage3 집계 — 기준선 anytime · OPT (되돌아가기 판, 2026-11-24 대기열) · OPT.Dec28 판 (stage3b) · 이력 규칙 첫 해 · 결정성.

사용: python -m opt.stage3_report out/val out/stage3 out/stage3b > out/stage3/summary.md
"""
import json
import sys
from pathlib import Path

SETS = [("base", "기본"), ("stair", "계단"), ("dense30", "밀집 30"), ("dense50", "밀집 50"), ("pair", "블록 쌍")]


def jl(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def passed_J(out_path):
    o = jl(out_path)
    if o is None:
        return None, None
    return ({r["pipe_id"] for r in o["routes"] if r["success"]},
            {r["pipe_id"]: r["J"]["total"] for r in o["routes"] if r["success"]})


def main(argv=None) -> int:
    val, s3, s3b = (argv or sys.argv[1:])[:3]
    man = json.loads((Path(val) / "manifest.json").read_text())
    cols = {"baseline": Path(s3) / "baseline", "opt_bt": Path(s3) / "opt_fixed", "opt_dec28": Path(s3b) / "opt"}
    rows = []
    for key, name in SETS:
        R = {"set": name, "n": 0}
        for c in cols:
            R[c] = {"first_pass": 0, "first_mb": 0, "best_pass": 0, "best_mb": 0, "best_full": 0, "best_J": 0.0,
                    "t0": [], "n": 0, "over_budget": 0}
        common = {"bJ": 0.0, "oJ": 0.0, "n": 0}
        hist = {"fixed": [0, 0.0], "history": [0, 0.0], "n": 0}
        det = [0, 0]
        for r in man["sets"][key]:
            stem = Path(r["file"]).stem
            R["n"] += 1
            for c, d in cols.items():
                s = jl(d / f"{stem}_summary.json")
                if s is None:
                    continue
                X = R[c]
                X["n"] += 1
                if s.get("first"):
                    X["first_pass"] += s["first"]["passed"]
                    X["first_mb"] += s["first"]["member_blockage"]
                X["best_pass"] += s["best"]["passed"]
                X["best_mb"] += s["best"]["member_blockage"]
                X["best_full"] += int(bool(s["best"]["full_success"]))
                X["best_J"] += s["best"]["J_total"]
                if s.get("t0_sec") is not None:
                    X["t0"].append(s["t0_sec"])
                rs = s.get("routing_sec")
                if rs is not None and rs > s["budget_sec"] + 1e-6:
                    X["over_budget"] += 1
            pb, Jb = passed_J(cols["baseline"] / f"{stem}_output.json")
            po, Jo = passed_J(cols["opt_dec28"] / f"{stem}_output.json")
            if pb is not None and po is not None:
                com = pb & po
                common["n"] += len(com)
                common["bJ"] += sum(Jb[p] for p in com)
                common["oJ"] += sum(Jo[p] for p in com)
            hf = jl(Path(s3) / "opt_fixed" / f"{stem}_summary.json")
            hh = jl(Path(s3) / "opt_first_history" / f"{stem}_summary.json")
            if hf and hh and hf.get("first") and hh.get("first"):
                hist["n"] += 1
                hist["fixed"][0] += hf["first"]["passed"]; hist["fixed"][1] += hf["first"]["J_total"]
                hist["history"][0] += hh["first"]["passed"]; hist["history"][1] += hh["first"]["J_total"]
            a = Path(s3b) / "opt" / f"{stem}_det.json"
            b = Path(s3b) / "w1" / "opt" / f"{stem}_det.json"
            if a.exists() and b.exists():
                det[1] += 1
                da, db = json.loads(a.read_text()), json.loads(b.read_text())
                for x in (da, db):
                    x["global"].pop("fixed_layers", None)
                det[0] += int(json.dumps(da) == json.dumps(db))
        R["common"], R["hist"], R["det"] = common, hist, det
        rows.append(R)

    print("| 세트 | 시나리오 | 부재 막힘 (예산 끝) 기준선 / OPT 되돌아가기 판 / OPT.Dec28 판 | 통과 (예산 끝) 기준선 / 되돌아가기 / Dec28 | ① 완전 성공 기준선 / 되돌아가기 / Dec28 | 통과 (첫 해) 기준선 / OPT | 부재 막힘 (첫 해) 기준선 / OPT |")
    print("|---|---|---|---|---|---|---|")
    for R in rows:
        b, o, d = R["baseline"], R["opt_bt"], R["opt_dec28"]
        print(f"| {R['set']} | {R['n']} | {b['best_mb']} / {o['best_mb']} / {d['best_mb']} | {b['best_pass']} / {o['best_pass']} / {d['best_pass']} "
              f"| {b['best_full']} / {o['best_full']} / {d['best_full']} | {b['first_pass']} / {o['first_pass']} | {b['first_mb']} / {o['first_mb']} |")
    print()
    print("| 세트 | J_total 예산 끝 기준선 / Dec28 (kg) | 공통 통과 배관 · J 차 (Dec28 − 기준선) | 첫 해 시간 중앙 기준선 / OPT (s) | 예산 넘김 기준선 / 되돌아가기 / Dec28 | 첫 해 고정 규칙 vs 이력 규칙 (통과 · J) | 결정성 Dec28 작업자 1 ↔ 2 |")
    print("|---|---|---|---|---|---|---|")
    import statistics as st
    for R in rows:
        b, o, d = R["baseline"], R["opt_bt"], R["opt_dec28"]
        c = R["common"]
        dj = (c["oJ"] / c["bJ"] - 1) * 100 if c["bJ"] else 0.0
        med = lambda v: f"{st.median(v):,.0f}" if v else "—"  # noqa: E731
        h = R["hist"]
        print(f"| {R['set']} | {b['best_J']:,.0f} / {d['best_J']:,.0f} | {c['n']} · {dj:+.2f} % | {med(b['t0'])} / {med(o['t0'])} "
              f"| {b['over_budget']} / {o['over_budget']} / {d['over_budget']} "
              f"| {h['fixed'][0]} · {h['fixed'][1]:,.0f} vs {h['history'][0]} · {h['history'][1]:,.0f} ({h['n']} 시나리오) "
              f"| {R['det'][0]} / {R['det'][1]} |")
    (Path(s3) / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
