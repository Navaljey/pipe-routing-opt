"""OPT.Stage1 측정 대기열 — E1 조건 (동시 실행 2, 밀집 50 은 한 번에 하나, skeleton D110 ① · D122).

단계 1: 전 시나리오 workers=1 (+ 충돌 검출) → 단계 2: 전 시나리오 workers=2. 단계 사이에 섞지 않는다.
사용: python -m opt.stage1_queue out/val -o out/stage1
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def jobs(val: Path) -> list:
    man = json.loads((val / "manifest.json").read_text())
    order = ["dense50", "pair", "dense30", "stair", "base"]   # 큰 일 먼저
    return [str(val / r["file"]) for s in order for r in man["sets"][s]]


def run_phase(files, workers, out, log, conc=2):
    pending, running = list(files), []
    while pending or running:
        running = [(f, p) for f, p in running if p.poll() is None]
        busy_d50 = any("dense50" in f for f, _ in running)
        while len(running) < conc and pending:
            pick = next((f for f in pending if not ("dense50" in f and busy_d50)), None)
            if pick is None:
                break
            pending.remove(pick)
            cmd = [sys.executable, "-m", "opt.stage1_measure", pick, "--workers", str(workers), "-o", out]
            if workers == 1:
                cmd.append("--conflicts")
            running.append((pick, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)))
            busy_d50 = busy_d50 or "dense50" in pick
            log.write(f"{time.strftime('%H:%M:%S')} start w{workers} {Path(pick).stem}\n"); log.flush()
        time.sleep(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("val")
    ap.add_argument("-o", "--out", default="out/stage1")
    ap.add_argument("--phases", default="1,2")
    a = ap.parse_args(argv)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    files = jobs(Path(a.val))
    with open(Path(a.out) / "run.log", "a") as log:
        for w in map(int, a.phases.split(",")):
            log.write(f"{time.strftime('%F %T')} phase w{w} begin\n"); log.flush()
            run_phase(files, w, a.out, log)
            log.write(f"{time.strftime('%F %T')} phase w{w} end\n"); log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
