"""OPT.Stage1 §7.2 미결 조사 — 단자 예약은 지키고 놓인 배관은 없는 단독 라우팅을 skeleton 수정 없이 부를 수 있는가.

검증용 시드 (40000000~, OPT.Req4) 시나리오 1개에서:
  ① layered_router_a(sc, pipe, t, placed=()) — 예약 대상 = 다른 배관 단자 2·(N−1) 개, 부재 예약 0 개 (graph_stats)
  ② 그 경로가 모든 예약 구간과 r₁ + r₂ 이상 (검증기와 같은 식 multi.routes_conflict)
  ③ router.without_reservations (= D101 단독) 와 비교 — 예약이 경로를 바꾼 배관 수
결과는 인터페이스 확인용이다. 서포트 표가 공개 임시 표면 측정값으로 쓰지 않는다 (OPT.Req9).
사용: python -m opt.probes.stage1_solo_reservation_probe [--seed 40000000] [--n-pipes 10]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skeleton"))

from pipe_routing.generator import GeneratorConfig, generate          # noqa: E402
from pipe_routing.layered import layered_router_a, terminal_reservations  # noqa: E402
from pipe_routing.multi import routes_conflict                        # noqa: E402
from pipe_routing.support_std import load_standard                    # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=40_000_000)
    ap.add_argument("--n-pipes", type=int, default=10)
    a = ap.parse_args(argv)
    assert 40_000_000 <= a.seed <= 49_999_999, "검증용 시드만 (OPT.Req4)"
    print("support_standard:", load_standard().info())
    sc = generate(GeneratorConfig(seed=a.seed, n_pipes=a.n_pipes), f"val_{a.seed}")
    R = layered_router_a
    n_changed = 0
    for p in sc.pipes:
        r = R(sc, p, 60.0, placed=())
        gs = getattr(r, "graph_stats", {}) or {}
        res = terminal_reservations(sc, exclude={p.id})
        viol = [q.id for q, seg in res if r.status == "ok" and routes_conflict(p, r.waypoints, q, seg)]
        f = R.without_reservations(sc, p, 60.0, placed=())
        same = (r.status == f.status) and (r.status != "ok" or r.waypoints == f.waypoints)
        n_changed += (not same)
        print(f"{p.id} {p.nominal_size:>5} T{'5' if p.gravity_pipe else '1'} res={r.status:<11} "
              f"exp={r.expanded:>8} members={gs.get('support_members', '?')} "
              f"expect_reserved={len(res)} reservation_violations={viol} free={f.status:<11} same_as_free={same}")
    print(f"예약이 결과를 바꾼 배관: {n_changed}/{len(sc.pipes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
