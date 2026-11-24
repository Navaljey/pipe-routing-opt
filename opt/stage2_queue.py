"""OPT.Stage2 측정 대기열 — E1 조건 (동시 실행 2, 밀집 50 은 한 번에 하나).

단계 A: 전 시나리오 × {기준선 첫 해, OPT 작업자 2}. 단계 B: 결정성 시험 — OPT 작업자 1 (기본 · 계단).
사용: python -m opt.stage2_queue out/val -o out/stage2
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ORDER = ["dense50", "pair", "dense30", "stair", "base"]


def run(jobs, out, log, conc=2):
    pending, running = list(jobs), []
    while pending or running:
        running = [(j, p) for j, p in running if p.poll() is None]
        busy = any("dense50" in j[0] for j, _ in running)
        while len(running) < conc and pending:
            pick = next((j for j in pending if not ("dense50" in j[0] and busy)), None)
            if pick is None:
                break
            pending.remove(pick)
            f, mode, w = pick
            cmd = [sys.executable, "-m", "opt.stage2_measure", f, "--mode", mode, "--workers", str(w), "-o", out]
            running.append((pick, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)))
            busy = busy or "dense50" in f
            log.write(f"{time.strftime('%F %T')} start {mode} w{w} {Path(f).stem}\n"); log.flush()
        time.sleep(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("val")
    ap.add_argument("-o", "--out", default="out/stage2")
    a = ap.parse_args(argv)
    man = json.loads((Path(a.val) / "manifest.json").read_text())
    files = {s: [str(Path(a.val) / r["file"]) for r in man["sets"][s]] for s in ORDER}
    A = [(f, m, 2 if m == "opt" else 1) for s in ORDER for f in files[s] for m in ("baseline", "opt")]
    B = [(f, "opt", 1) for s in ("stair", "base") for f in files[s]]
    Path(a.out).mkdir(parents=True, exist_ok=True)
    with open(Path(a.out) / "run.log", "a") as log:
        for name, jobs in (("A", A), ("B", B)):
            log.write(f"{time.strftime('%F %T')} phase {name} begin ({len(jobs)})\n"); log.flush()
            run(jobs, a.out, log)
            log.write(f"{time.strftime('%F %T')} phase {name} end\n"); log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
