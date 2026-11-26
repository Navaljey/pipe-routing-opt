"""OPT.Stage3 점검 반영판 측정 대기열 — 2026-11-24 대기열 (opt.stage3_queue) 이 끝난 뒤에 시작한다 (동시 실행 2 유지).

A: 전 시나리오 OPT.Dec28 + D139 (작업자 2)  B: 결정성 — 기본 · 계단 OPT.Dec28 작업자 1
기준선은 2026-11-24 대기열의 기준선 anytime 결과를 쓴다 (같은 컨테이너).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ORDER = ["dense50", "pair", "dense30", "stair", "base"]


def done(out, f, mode, w):
    base = Path(out) if w == 2 else Path(out) / f"w{w}"
    return (base / mode / f"{Path(f).stem}_summary.json").exists()


def run(jobs, out, log, conc=2):
    pending = [j for j in jobs if not done(out, *j)]
    running = []
    while pending or running:
        running = [(j, p) for j, p in running if p.poll() is None]
        busy = any("dense50" in j[0] for j, _ in running)
        while len(running) < conc and pending:
            pick = next((j for j in pending if not ("dense50" in j[0] and busy)), None)
            if pick is None:
                break
            pending.remove(pick)
            f, mode, w = pick
            o = out if w == 2 else str(Path(out) / f"w{w}")
            cmd = [sys.executable, "-m", "opt.stage3b_measure", f, "--mode", mode, "--workers", str(w), "-o", o]
            running.append((pick, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)))
            busy = busy or "dense50" in f
            log.write(f"{time.strftime('%F %T')} start {mode} w{w} {Path(f).stem}\n"); log.flush()
        time.sleep(5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("val")
    ap.add_argument("-o", "--out", default="out/stage3b")
    ap.add_argument("--wait-for", default="out/stage3/run.log", help="이 로그에 'phase C end' 가 나올 때까지 기다린다")
    a = ap.parse_args(argv)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    with open(Path(a.out) / "run.log", "a") as log:
        log.write(f"{time.strftime('%F %T')} waiting for {a.wait_for}\n"); log.flush()
        while "phase C end" not in Path(a.wait_for).read_text():
            time.sleep(30)
        man = json.loads((Path(a.val) / "manifest.json").read_text())
        files = {s: [str(Path(a.val) / r["file"]) for r in man["sets"][s]] for s in ORDER}
        A = [(f, "opt", 2) for s in ORDER for f in files[s]]
        B = [(f, "opt", 1) for s in ("stair", "base") for f in files[s]]
        for name, jobs in (("A", A), ("B", B)):
            log.write(f"{time.strftime('%F %T')} phase {name} begin ({len(jobs)})\n"); log.flush()
            run(jobs, a.out, log)
            log.write(f"{time.strftime('%F %T')} phase {name} end\n"); log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
