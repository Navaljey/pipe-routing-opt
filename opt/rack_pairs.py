"""참고 지표 (OPT.Question19 공지 2) — 해 안에서 D82 로 묶일 수 있었던 서포트 쌍 수.

skeleton verifier.hint_bench 의 "묶음 후보 쌍" 규칙을 그대로 쓴다: 같은 면 C 서포트 · 같은 축 정렬 수평 축 ·
축 위치 ±WINDOW_MM · 가로 거리 ≤ L1 표 끝 → SupportHint.groupable 로 D82 조건 확인 (간격 재확인 D82 보완은 보지 않음).
검증기 · 힌트 결과만 세고 표 수치는 남기지 않는다. 측정 시간 밖 (사후 계산).
사용: python -m opt.rack_pairs out/val out/stage2 baseline opt_w2
"""
import json
import sys
from pathlib import Path

from . import _sk  # noqa: F401
from pipe_routing.scenario import load  # noqa: E402
from pipe_routing.support_std import load_standard  # noqa: E402
from pipe_routing.verifier import PipeRoute  # noqa: E402
from pipe_routing.verifier import support_group as sg  # noqa: E402
from pipe_routing.verifier.hint import SupportHint  # noqa: E402
from pipe_routing.verifier.hint_bench import _exact, _verify_recording  # noqa: E402


def pairs_of(sc, wps: dict, std) -> dict:
    routes = [PipeRoute(pid, w) for pid, w in sorted(wps.items()) if w]
    reps = _verify_recording(sc, routes, std)
    h = SupportHint(sc, routes, std=std)
    cs = [(pid, s) for pid, rep in reps.items() for s in rep.supports if s["form"] == "C" and not s.get("origin")]
    d_groups = len({s["group"] for rep in reps.values() for s in rep.supports if s.get("group")})
    lim = sg.l1_max(std)
    n, ok, why = 0, 0, {}
    for i, (pa, sa) in enumerate(cs):
        ua = sa["axis"]
        if abs(ua[2]) > 1e-9 or max(abs(v) for v in ua) < 1 - 1e-9:
            continue
        ax = max(range(3), key=lambda k: abs(ua[k]))
        for pb, sb in cs[i + 1:]:
            if pb == pa or sb["face"] != sa["face"] or sb["axis"] != ua or \
                    abs(sa["pos"][ax] - sb["pos"][ax]) > sg.WINDOW_MM or abs(sa["pos"][1 - ax] - sb["pos"][1 - ax]) > lim:
                continue
            n += 1
            g = h.groupable((pa, *_exact(sa)), (pb, *_exact(sb)), face=sa["face"])
            ok += int(g["ok"])
            if not g["ok"]:
                why[g["reason"]] = why.get(g["reason"], 0) + 1
    return {"candidate_pairs": n, "groupable_ok": ok, "why_not": why, "d_groups_in_solution": d_groups}


def main(argv=None) -> int:
    val, out, *modes = argv or sys.argv[1:]
    man = json.loads((Path(val) / "manifest.json").read_text())
    std = load_standard()
    res = {}
    for s, rows in man["sets"].items():
        for r in rows:
            stem = Path(r["file"]).stem
            for m in modes:
                f = Path(out) / m / f"{stem}_det.json"
                if not f.exists():
                    continue
                det = json.loads(f.read_text())
                wps = {x["pipe_id"]: x["waypoints"] for x in det["routes"] if x["waypoints"]}
                res.setdefault(m, {}).setdefault(s, {})[stem] = pairs_of(load(str(Path(val) / r["file"])), wps, std)
                print(m, stem, res[m][s][stem], flush=True)
    (Path(out) / "rack_pairs.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
