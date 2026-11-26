"""OPT.Dec21 · Dec34 — D135 분모 정정 (고정 커밋 55b0e35 의 `reclassify.reclassify_output` · `required_pipe_ids`).

경로 · J 는 다시 돌리지 않고 분류만 다시 계산한다 (skeleton E1.1 과 같은 방식). 새로 필수가 된 배관은 옛 실행에서
라우팅하지 않았으므로 미통과로 센다. 사용: python -m opt.d135_recount -o out/d135_recount.json
"""
import json
import sys
from pathlib import Path

from . import _sk  # noqa: F401
from pipe_routing.pipeline import required_pipe_ids  # noqa: E402
from pipe_routing.reclassify import reclassify_output  # noqa: E402
from pipe_routing.scenario import load  # noqa: E402

RUNS = {"stage2_baseline_first": ("out/stage2/baseline", "_det.json"),
        "stage2_opt_first": ("out/stage2/opt_w2", "_det.json"),
        "stage3_baseline_first": ("out/stage3/baseline", "_first_output.json"),
        "stage3_baseline_end": ("out/stage3/baseline", "_output.json"),
        "stage3_opt_dec28_end": ("out/stage3b/opt", "_output.json")}


def main(argv=None):
    out = Path((argv or sys.argv[1:] + ["-o", "out/d135_recount.json"])[-1])
    man = json.loads(Path("out/val/manifest.json").read_text())
    res = {}
    for s, rows in man["sets"].items():
        for r in rows:
            stem = Path(r["file"]).stem
            sc = load(str(Path("out/val") / r["file"]))
            req, exc = required_pipe_ids(sc)
            row = {"set": s, "required": len(req), "excluded": sorted(exc)}
            for k, (d, suf) in RUNS.items():
                f = Path(d) / f"{stem}{suf}"
                if f.exists():
                    o = json.loads(f.read_text())
                    g0 = o["global"]
                    g = reclassify_output(o, sc)["global"]
                    row[k] = {"required_passed_old": g0["required_passed"], "required_old": g0["required_pipes"],
                              "required_passed": g["required_passed"], "full_success": bool(g["full_success"]),
                              "passed": g["success_count"], "J_total": g["J_total"]}
            res[stem] = row
    tab = {}
    for stem, row in res.items():
        t = tab.setdefault(row["set"], {"n": 0, "required": 0})
        t["n"] += 1
        t["required"] += row["required"]
        for k in RUNS:
            if k in row:
                x = t.setdefault(k, {"required_passed": 0, "full_success": 0, "required_passed_old": 0, "required_old": 0})
                for f in x:
                    x[f] += int(row[k][f])
    out.write_text(json.dumps({"per": res, "sets": tab}, ensure_ascii=False, indent=1))
    print(json.dumps(tab, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
