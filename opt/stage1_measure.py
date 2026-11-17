"""OPT.Stage1 측정 — 시나리오 1개: 루트 (workers 1 또는 2) → JSON, workers=1 이면 충돌 검출까지.

사용: python -m opt.stage1_measure <scenario.json> --workers 1|2 -o out/stage1
심판 일치 확인 (OPT.Req9) 을 통과하지 않으면 실행하지 않는다.
"""
import argparse
import json
import platform
from pathlib import Path

from . import _sk  # noqa: F401
from .judge_check import check
from .root import build_root
from .conflicts import detect


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--conflicts", action="store_true", help="충돌 검출도 (루트 시간과 따로 잰다)")
    ap.add_argument("-o", "--out", default="out/stage1")
    a = ap.parse_args(argv)
    jc = check()
    if not all(v["ok"] for v in jc.values()):
        raise SystemExit(f"심판 불일치 — 실행하지 않는다: {jc}")
    root = build_root(a.scenario, a.workers)
    root["judge"] = {"skeleton": jc["skeleton_commit"]["value"], "support_sha256": jc["support_standard"]["sha256"],
                     "support_source": jc["support_standard"]["source"]}
    root["machine"] = {"python": platform.python_version(), "cpu_count": __import__("os").cpu_count()}
    import resource
    root["max_rss_mb"] = {"main": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                          "workers_max": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024}
    if a.conflicts:
        root["conflicts"] = detect(a.scenario, root)
    out = Path(a.out) / f"w{a.workers}"
    out.mkdir(parents=True, exist_ok=True)
    (out / (Path(a.scenario).stem + "_root.json")).write_text(json.dumps(root, ensure_ascii=False))
    s = {k: root[k] for k in ("workers", "root_wall_sec", "expanded_total")}
    print(Path(a.scenario).stem, s, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
