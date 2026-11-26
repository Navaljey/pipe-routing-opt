"""OPT.Req12 고정 커밋 갱신 확인 — E1 첫 해 41 바이트 동일 (심판 일치 확인, 측정 아님 · 결과로 파라미터를 바꾸지 않는다).

옛 고정 커밋 (cd9c13e, 작업 트리 --old) 과 새 고정 커밋 (서브모듈 skeleton/) 에서 E1 41 시나리오를 기준선 첫 해
(`pipe_routing.pipeline` 순차 · E1 조건 · --no-solo-ref · --no-viz) 로 돌리고, 새 커밋의 `deterministic_view` 로 비교한다.
D101 단독 참조 (`routes[].solo`) 는 시간을 아끼려 양쪽 모두 끈다 (첫 해 경로 · J · 통과에는 영향 없음).
대기열: 동시 3, 밀집 50 은 한 번에 하나 (메모리). 끝난 시나리오는 건너뛴다 (컨테이너 재시작 뒤 이어 돌리기).
사용: python -m opt.e1_first_check run --old /tmp/claude-0/sk_old -o out/e1_check
      python -m opt.e1_first_check compare -o out/e1_check
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import _sk  # noqa: F401

ROOT = _sk.ROOT
SK = _sk.SKELETON


def scenarios():
    ref = json.loads((SK / "docs/reports/s1_8_scenario_sha256.json").read_text())
    return [(s, rel) for s, files in ref.items() for rel in files]


def run(old: Path, out: Path, conc: int):
    jobs = []
    for s, rel in scenarios():
        for tag, d in (("old", old), ("new", SK)):
            o = out / tag
            if (o / f"{Path(rel).stem}_output.json").exists():
                continue
            jobs.append((0 if s == "dense50" else 1, s, rel, tag, d, o))
    jobs.sort(key=lambda j: (j[0], j[2], j[3]))
    running = []
    env = dict(os.environ)
    while jobs or running:
        running = [r for r in running if r[0].poll() is None]
        d50 = sum(1 for r in running if r[1] == "dense50")
        while len(running) < conc and jobs:
            j = next((j for j in jobs if j[1] != "dense50" or d50 == 0), None)
            if j is None:
                break
            jobs.remove(j)
            _, s, rel, tag, d, o = j
            o.mkdir(parents=True, exist_ok=True)
            log = open(o / f"{Path(rel).stem}.log", "w")
            p = subprocess.Popen([sys.executable, "-m", "pipe_routing.pipeline", str(d / rel), "-o", str(o),
                                  "--no-viz", "--no-solo-ref"], cwd=str(d), env=env, stdout=log, stderr=subprocess.STDOUT)
            running.append((p, s, rel, tag, time.time()))
            d50 += s == "dense50"
            print(time.strftime("%H:%M:%S"), "start", tag, rel, flush=True)
        time.sleep(5)
    print("all done", flush=True)


def compare(out: Path):
    from pipe_routing.pipeline import deterministic_view
    res, bad = {}, []
    for s, rel in scenarios():
        stem = Path(rel).stem
        fo, fn = out / "old" / f"{stem}_output.json", out / "new" / f"{stem}_output.json"
        if not (fo.exists() and fn.exists()):
            res[stem] = None
            continue
        a = json.dumps(deterministic_view(json.loads(fo.read_text())), sort_keys=True)
        b = json.dumps(deterministic_view(json.loads(fn.read_text())), sort_keys=True)
        res[stem] = a == b
        if a != b:
            bad.append(stem)
    summ = {"compared": sum(v is not None for v in res.values()), "identical": sum(v is True for v in res.values()),
            "different": bad, "missing": [k for k, v in res.items() if v is None]}
    (out / "compare.json").write_text(json.dumps({"summary": summ, "per": res}, ensure_ascii=False, indent=1))
    print(json.dumps(summ, ensure_ascii=False))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "compare"])
    ap.add_argument("--old", default="/tmp/claude-0/sk_old")
    ap.add_argument("-o", "--out", default="out/e1_check")
    ap.add_argument("--concurrency", type=int, default=3)
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    if a.cmd == "run":
        run(Path(a.old), out, a.concurrency)
        return 0
    return compare(out)


if __name__ == "__main__":
    raise SystemExit(main())
