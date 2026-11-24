"""OPT.Stage3 측정 대기열 — E1 조건 (동시 실행 2, 밀집 50 은 한 번에 하나). 이미 끝난 작업 (요약 파일 있음) 은 건너뛴다.

A: 전 시나리오 × {기준선 anytime, OPT anytime (고정 규칙)}  B: OPT 첫 해 (이력 규칙, OPT.Dec23 비교)
C: 결정성 — 기본 세트 OPT anytime 작업자 1
사용: python -m opt.stage3_queue out/val -o out/stage3
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ORDER = ["dense50", "pair", "dense30", "stair", "base"]


def done(out, f, mode, rule, w):
    stem = Path(f).stem
    base = Path(out) if w == 2 else Path(out) / f"w{w}"
    d = "baseline" if mode == "baseline" else f"{mode}_{rule}"
    return (base / d / f"{stem}_summary.json").exists()


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
            f, mode, rule, w = pick
            o = out if w == 2 else str(Path(out) / f"w{w}")
            cmd = [sys.executable, "-m", "opt.stage3_measure", f, "--mode", mode, "--rule", rule, "--workers", str(w), "-o", o]
            running.append((pick, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)))
            busy = busy or "dense50" in f
            log.write(f"{time.strftime('%F %T')} start {mode} {rule} w{w} {Path(f).stem}\n"); log.flush()
        time.sleep(5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("val")
    ap.add_argument("-o", "--out", default="out/stage3")
    a = ap.parse_args(argv)
    man = json.loads((Path(a.val) / "manifest.json").read_text())
    files = {s: [str(Path(a.val) / r["file"]) for r in man["sets"][s]] for s in ORDER}
    A = [(f, m, "fixed", 2) for s in ORDER for f in files[s] for m in ("baseline", "opt")]
    B = [(f, "opt_first", "history", 2) for s in ORDER for f in files[s]]
    C = [(f, "opt", "fixed", 1) for f in files["base"]]
    Path(a.out).mkdir(parents=True, exist_ok=True)
    with open(Path(a.out) / "run.log", "a") as log:
        for name, jobs in (("A", A), ("B", B), ("C", C)):
            log.write(f"{time.strftime('%F %T')} phase {name} begin ({len(jobs)})\n"); log.flush()
            run(jobs, a.out, log)
            log.write(f"{time.strftime('%F %T')} phase {name} end\n"); log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
