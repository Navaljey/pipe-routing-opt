"""OPT.Stage1 측정 집계 → 세트별 표 (markdown) + 요약 JSON.

사용: python -m opt.stage1_report out/val out/stage1 > out/stage1/summary.md
"""
import json
import statistics as st
import sys
from pathlib import Path

from .root import deterministic_view

SETS = [("base", "기본"), ("stair", "계단"), ("dense30", "밀집 30"), ("dense50", "밀집 50"), ("pair", "블록 쌍")]


def load(out, w, stem):
    p = Path(out) / f"w{w}" / f"{stem}_root.json"
    return json.loads(p.read_text()) if p.exists() else None


def main(argv=None) -> int:
    val, out = (argv or sys.argv[1:])[:2]
    man = json.loads((Path(val) / "manifest.json").read_text())
    rows, summ = [], {}
    for key, name in SETS:
        R = {"set": name, "scen": 0, "pipes": 0, "excl": 0, "ok": 0, "unreach": 0, "timeout": 0,
             "c1": 0, "c2": 0, "c3": 0, "c3_single": 0, "c3_multi": 0, "c3_none": 0, "pairs": 0, "inv": 0,
             "solo_pass": 0, "multi_pass": 0, "w1": [], "w2": [], "cpu1": 0.0, "cpu2": 0.0, "exp": 0,
             "same": 0, "both": 0, "csec": 0.0, "rss1": 0.0, "rss2": 0.0, "missing": []}
        for r in man["sets"][key]:
            stem = Path(r["file"]).stem
            a, b = load(out, 1, stem), load(out, 2, stem)
            if a is None:
                R["missing"].append(stem)
                continue
            R["scen"] += 1
            ps = a["pipes"]
            R["pipes"] += len(ps)
            for x in ps:
                k = {"excluded": "excl", "ok": "ok", "unreachable": "unreach", "timeout": "timeout"}[x["status"]]
                R[k] += 1
            c = a["conflicts"]
            R["c1"] += len(c["conflict1_pairs"]); R["c2"] += len(c["conflict2_pairs"])
            R["c3"] += len(c["conflict3_pairs"])
            for y in c["conflict3"]:
                R["c3_" + y["kind"]] += 1
            R["pairs"] += c["pairs_any"]; R["inv"] += len(c["pipes_in_conflict"])
            R["solo_pass"] += c["solo_pass"]; R["multi_pass"] += c["multi_pass"]
            R["csec"] += c["sec"]["total"]
            R["w1"].append(a["root_wall_sec"]); R["cpu1"] += sum(a["cpu_sec_workers"]); R["exp"] += a["expanded_total"]
            R["rss1"] = max(R["rss1"], a["max_rss_mb"]["main"])
            if b is None:
                R["missing"].append(stem + "@w2")
                continue
            R["both"] += 1
            R["same"] += json.dumps(deterministic_view(a)) == json.dumps(deterministic_view(b))
            R["w2"].append(b["root_wall_sec"]); R["cpu2"] += sum(b["cpu_sec_workers"])
            R["rss2"] = max(R["rss2"], b["max_rss_mb"]["workers_max"])
        rows.append(R)
        summ[key] = R

    def f(x, d=0):
        return f"{x:,.{d}f}"

    print("| 세트 | 시나리오 | 배관 | 분모 제외 | 루트 경로 | 단독 실패 (경로 없음 / 상한 소진) | 단독 검증 통과 | 루트 전부 함께 검증 통과 |")
    print("|---|---|---|---|---|---|---|---|")
    for R in rows:
        print(f"| {R['set']} | {R['scen']} | {R['pipes']} | {R['excl']} | {R['ok']} | {R['unreach'] + R['timeout']} "
              f"({R['unreach']} / {R['timeout']}) | {R['solo_pass']} | {R['multi_pass']} |")
    print()
    print("| 세트 | 충돌 1 관 간섭 (쌍) | 충돌 2 단자 예약 침범 (쌍) | 충돌 3 서포트 충돌 (쌍) | 서포트 충돌 배관 (단일 / 복수 / 불명) | 충돌 쌍 합 (중복 제외) | 충돌에 걸린 배관 / 루트 경로 | 시나리오당 충돌 쌍 (중앙 아님, 평균) |")
    print("|---|---|---|---|---|---|---|---|")
    for R in rows:
        ratio = R["inv"] / R["ok"] * 100 if R["ok"] else 0
        print(f"| {R['set']} | {R['c1']} | {R['c2']} | {R['c3']} | {R['c3_single'] + R['c3_multi'] + R['c3_none']} "
              f"({R['c3_single']} / {R['c3_multi']} / {R['c3_none']}) | {R['pairs']} | {R['inv']} / {R['ok']} "
              f"({ratio:.1f}%) | {R['pairs'] / max(R['scen'], 1):.1f} |")
    print()
    print("| 세트 | 루트 벽시계 순차 합 (s) | 순차 시나리오 중앙 / 최대 (s) | 작업자 2 합 (s) | 작업자 2 중앙 / 최대 (s) | 합 비 (2 / 1) | CPU 합 순차 / 작업자 2 (s) | 확장 합 | 결정성 (같음 / 둘 다 잰 시나리오) | 충돌 검출 합 (s) | 최대 RSS 순차 / 작업자 (GB) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for R in rows:
        w1, w2 = R["w1"], R["w2"]
        md = lambda v: f"{st.median(v):,.0f} / {max(v):,.0f}" if v else "—"  # noqa: E731
        ratio = (sum(w2) / sum(w1[:len(w2)])) if w2 else float("nan")
        print(f"| {R['set']} | {f(sum(w1))} | {md(w1)} | {f(sum(w2))} | {md(w2)} | {ratio:.2f} | {f(R['cpu1'])} / {f(R['cpu2'])} "
              f"| {f(R['exp'])} | {R['same']} / {R['both']} | {f(R['csec'])} | {R['rss1'] / 1024:.1f} / {R['rss2'] / 1024:.1f} |")
    miss = [m for R in rows for m in R["missing"]]
    if miss:
        print(f"\n빠진 결과: {miss}")
    (Path(out) / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
