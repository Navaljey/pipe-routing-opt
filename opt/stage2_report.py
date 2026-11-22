"""OPT.Stage2 집계 → 세트별 표 (markdown) + summary.json.

사용: python -m opt.stage2_report out/val out/stage2 > out/stage2/summary.md
"""
import json
import statistics as st
import sys
from pathlib import Path

SETS = [("base", "기본"), ("stair", "계단"), ("dense30", "밀집 30"), ("dense50", "밀집 50"), ("pair", "블록 쌍")]


def jload(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def jsum(det, ids):
    return sum(x["J"]["total"] for x in det["routes"] if x["pipe_id"] in ids)


def q(v, f):
    if not v:
        return "—"
    v = sorted(v)
    return f"{st.median(v):{f}} / {v[min(len(v) - 1, int(0.9 * len(v)))]:{f}} / {v[-1]:{f}}"


def main(argv=None) -> int:
    val, out = (argv or sys.argv[1:])[:2]
    man = json.loads((Path(val) / "manifest.json").read_text())
    rows, per = [], {}
    for key, name in SETS:
        R = dict(set=name, n=0, b_pass=0, o_pass=0, b_full=0, o_full=0, b_J=0.0, o_J=0.0, cJ_b=0.0, cJ_o=0.0,
                 common=0, b_t=[], o_t=[], o_root=[], o_search=[], ge=0, worse=[], first=0, exh=0,
                 rr=[], rr_to=0, rr_calls=0, dropped=0, rounds=[], newc=0, det_same=0, det_n=0, missing=[])
        for r in man["sets"][key]:
            stem = Path(r["file"]).stem
            b = jload(Path(out) / "baseline" / f"{stem}_summary.json")
            o = jload(Path(out) / "opt_w2" / f"{stem}_summary.json")
            if b is None or o is None:
                R["missing"].append(stem)
                continue
            R["n"] += 1
            R["b_pass"] += b["passed"]; R["o_pass"] += o["passed"]
            R["b_full"] += int(b["full_success"]); R["o_full"] += int(o["full_success"])
            R["b_J"] += b["J_total"]; R["o_J"] += o["J_total"]
            R["b_t"].append(b["routing_sec"]); R["o_t"].append(o["routing_sec"])
            R["o_root"].append(o["root_sec"]); R["o_search"].append(o["search_sec"])
            R["ge"] += int(o["passed"] >= b["passed"])
            if o["passed"] < b["passed"]:
                R["worse"].append(f"{stem} ({o['passed']} < {b['passed']})")
            R["first"] += int(o["first_solution"]); R["exh"] += int(o["budget_exhausted"])
            bd = jload(Path(out) / "baseline" / f"{stem}_det.json")
            od = jload(Path(out) / "opt_w2" / f"{stem}_det.json")
            ok_b = {x["pipe_id"] for x in bd["routes"] if x["success"]}
            ok_o = {x["pipe_id"] for x in od["routes"] if x["success"]}
            com = ok_b & ok_o
            R["common"] += len(com); R["cJ_b"] += jsum(bd, com); R["cJ_o"] += jsum(od, com)
            lg = jload(Path(out) / "opt_w2" / f"{stem}_opt_log.json")
            R["rr"] += [x["sec"] for x in lg["reroutes"]]
            R["rr_calls"] += len(lg["reroutes"])
            R["rr_to"] += sum(1 for x in lg["reroutes"] if x["status"] == "timeout")
            R["dropped"] += sum(1 for v in lg["failed"].values() if v == "coexist")
            R["rounds"].append(len(lg["rounds"]))
            R["newc"] += sum(lg["new_conflicts_after_recheck"])
            w1 = Path(out) / "opt_w1" / f"{stem}_det.json"
            if w1.exists():
                R["det_n"] += 1
                R["det_same"] += int(w1.read_bytes() == (Path(out) / "opt_w2" / f"{stem}_det.json").read_bytes())
            per[stem] = {"baseline": b, "opt": o, "common_pass": len(com)}
        rows.append(R)

    print("| 세트 | 시나리오 | 통과 기준선 / OPT | OPT ≥ 기준선 시나리오 | ① 완전 성공 기준선 / OPT | J_total 기준선 / OPT (kg) | 공통 통과 배관 · J 기준선 / OPT (kg) | 공통 J 차 |")
    print("|---|---|---|---|---|---|---|---|")
    for R in rows:
        d = (R["cJ_o"] / R["cJ_b"] - 1) * 100 if R["cJ_b"] else 0
        print(f"| {R['set']} | {R['n']} | {R['b_pass']} / {R['o_pass']} | {R['ge']} / {R['n']} | {R['b_full']} / {R['o_full']} "
              f"| {R['b_J']:,.0f} / {R['o_J']:,.0f} | {R['common']} · {R['cJ_b']:,.0f} / {R['cJ_o']:,.0f} | {d:+.2f} % |")
    print()
    print("| 세트 | 첫 해 시간 기준선 중앙 / p90 / 최대 (s) | 첫 해 시간 OPT 중앙 / p90 / 최대 (s) | 합 기준선 / OPT (s) | OPT 루트 중앙 (s) | OPT 탐색 중앙 (s) | 첫 해 도달 / 예산 소진 |")
    print("|---|---|---|---|---|---|---|")
    for R in rows:
        print(f"| {R['set']} | {q(R['b_t'], ',.0f')} | {q(R['o_t'], ',.0f')} | {sum(R['b_t']):,.0f} / {sum(R['o_t']):,.0f} "
              f"| {st.median(R['o_root']) if R['o_root'] else 0:,.0f} | {st.median(R['o_search']) if R['o_search'] else 0:,.0f} "
              f"| {R['first']} / {R['exh']} |")
    print()
    print("| 세트 | 재라우팅 호출 | 재라우팅 시간 합 (s) | 1 회 중앙 / p90 / 최대 (s) | 재라우팅 중 상한 소진 | 함께 놓을 수 없어 실패로 둔 배관 | 전체 재검사 라운드 (중앙 / 최대) | 재검사에서 새로 생긴 충돌 쌍 | 결정성 작업자 1 ↔ 2 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for R in rows:
        rd = R["rounds"]
        print(f"| {R['set']} | {R['rr_calls']} | {sum(R['rr']):,.0f} | {q(R['rr'], '.1f')} | {R['rr_to']} | {R['dropped']} "
              f"| {st.median(rd) if rd else 0} / {max(rd) if rd else 0} | {R['newc']} | {R['det_same']} / {R['det_n']} |")
    worse = [w for R in rows for w in R["worse"]]
    if worse:
        print(f"\n기준선보다 통과 수가 적은 시나리오: {', '.join(worse)}")
    miss = [m for R in rows for m in R["missing"]]
    if miss:
        print(f"\n빠진 결과: {miss}")
    (Path(out) / "summary.json").write_text(json.dumps({"sets": rows, "per_scenario": per}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
