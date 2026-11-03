"""OPT.Req9 심판 일치 확인. 하나라도 다르면 0 이 아닌 값으로 끝난다 — 그때는 실행하지 않고 보고한다 (CLAUDE.md §10-3).

확인: ① skeleton 서브모듈 커밋 = cd9c13e (OPT.Dec9) ② E1 시나리오 41개 sha256 = skeleton docs/reports/s1_8_scenario_sha256.json
③ 서포트 표 source = private, sha256 = E1 동결값 (PIPE_SUPPORT_STANDARD).
사용: python -m opt.judge_check
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SK = ROOT / "skeleton"
SKELETON_COMMIT = "cd9c13e527935c43d79e6e60895260264617adba"
SUPPORT_SHA256 = "ac2ba6a2974b381938212b84867f1d6f34d87e885b2e1da5a0fc302cb19cffb3"


def check() -> dict:
    out = {}
    head = subprocess.run(["git", "-C", str(SK), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out["skeleton_commit"] = {"ok": head == SKELETON_COMMIT, "value": head}
    ref = json.loads((SK / "docs/reports/s1_8_scenario_sha256.json").read_text())
    bad, n = [], 0
    for files in ref.values():
        for rel, h in files.items():
            n += 1
            if hashlib.sha256((SK / rel).read_bytes()).hexdigest() != h:
                bad.append(rel)
    out["scenarios"] = {"ok": n == 41 and not bad, "count": n, "mismatch": bad}
    sys.path.insert(0, str(SK))
    from pipe_routing.support_std import load_standard
    info = load_standard().info()
    out["support_standard"] = {"ok": info["source"] == "private" and info["sha256"] == SUPPORT_SHA256, **info}
    return out


def main() -> int:
    res = check()
    print(json.dumps(res, ensure_ascii=False, indent=1))
    ok = all(v["ok"] for v in res.values())
    print("심판 일치:", "예" if ok else "아니오 — 실행하지 않는다 (§10-3)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
