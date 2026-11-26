"""조율 학습 기록 `coord_record/1` 쓰기 (docs/design/coordination_training_record.md, OPT.Dec25 · Dec26 · Dec31).

- 기록은 시드 31000000 ~ 31999999 에서만, 환경변수 OPT_COORD_RECORD_DIR (레포 밖) 가 있을 때만 켠다 (§1 · §2).
  그 밖의 시드 (검증용 40M · 평가) 에서는 켜지지 않고, 로그에 한 줄 남긴다.
- 한 줄 = 기록 하나. 필드는 쓰임 표시 묶음 {"id", "meta", "feat", "label"} 에 나눠 담는다 (§3).
  망 입력은 "feat" 만 — 식별자 ("id") 는 기록끼리 잇는 데만 쓴다.
- 아직 계산하지 않는 필드는 null 로 두고 머리 기록 meta.not_computed 에 이름을 적는다 (값을 지어내지 않는다).
- 탐색 ε (§3.4) 는 스위치만 있고 0 이다 (OPT.Dec31).
"""
import json
import os
from pathlib import Path

SCHEMA = "coord_record/1"
RECORD_SEEDS = (31_000_000, 31_999_999)

NOT_COMPUTED = [
    "entity.feat.props (섹션 4) 성질 표 — skeleton 조회 함수가 아직 없음)",
    "yield.feat.conflict.overlap_mm · position",
    "yield.feat.context (단자까지 거리 · 강제 직관 / 첫 꺾임 구역 · 주변 빈 부피)",
    "yield.feat.relation 중 나란히 감 · 표면 높이 차 · groupable · 서포트 자리 막힘 (양방향) · 같은 서비스 · 같은 계통",
    "yield.label 나중 결과 (내려가지 않은 쪽은 정의상 null)",
]


def record_enabled(seed):
    """(켜짐 여부, 이유)."""
    d = os.environ.get("OPT_COORD_RECORD_DIR", "")
    if seed is None or not (RECORD_SEEDS[0] <= int(seed) <= RECORD_SEEDS[1]):
        return False, f"seed {seed} 는 기록 범위 {RECORD_SEEDS[0]}~{RECORD_SEEDS[1]} 밖 — 기록 끔"
    if not d:
        return False, "OPT_COORD_RECORD_DIR 없음 — 기록 끔"
    repo = Path(__file__).resolve().parents[1]
    if Path(d).resolve().is_relative_to(repo):
        return False, "OPT_COORD_RECORD_DIR 가 레포 안 — 기록 끔 (레포 밖이어야 한다)"
    return True, "기록 켬"


class Recorder:
    def __init__(self, sc, stem, header_meta: dict):
        self.sc, self.stem = sc, stem
        self.rows = []
        self.ref = {}
        self.header = {"kind": "header", "meta": dict(header_meta, schema=SCHEMA, epsilon=0.0,
                                                    not_computed=NOT_COMPUTED, unit="mm")}

    def entities(self, root_routes, spec_of):
        for i, p in enumerate(sorted(self.sc.pipes, key=lambda p: p.id)):
            self.ref[p.id] = i
            r = root_routes.get(p.id)
            s = spec_of(p)
            dist = sum((a - b) ** 2 for a, b in zip(p.start.pos, p.end.pos)) ** 0.5
            self.rows.append({"kind": "entity", "id": {"ref": i, "name": p.id, "leg": "main"},
                              "feat": {"entity": "pipe", "props": None,
                                       "spec": {"nominal_mm": int(p.nominal_size[:-1]), "od_mm": s.od,
                                                "radius_mm": round(p.radius, 3), "kg_m": s.kg_per_m,
                                                "gravity": bool(p.gravity_pipe), "reducer": False,
                                                "insulation_mm": p.insulation_thickness},
                                       "terminals": {"start": {"pos": list(p.start.pos), "dir": list(p.start.dir),
                                                               "kind": p.start.kind},
                                                     "end": {"pos": list(p.end.pos), "dir": list(p.end.dir),
                                                             "kind": p.end.kind},
                                                     "distance_mm": round(dist, 1)},
                                       "solo": {"status": r.status if r is not None else "excluded",
                                                "expanded": int(r.expanded or 0) if r is not None else None,
                                                "J_router": float(r.J) if (r is not None and r.J is not None) else None,
                                                "support_est": float(r.J_support_est or 0.0) if r is not None else None}}})
        base = len(self.ref)
        for j, o in enumerate(self.sc.obstacles):
            lo, hi = list(o.box.min), list(o.box.max)
            self.rows.append({"kind": "entity", "id": {"ref": base + j, "name": o.id},
                              "feat": {"entity": "obstacle", "props": None, "attrs": None,
                                       "size_mm": [b - a for a, b in zip(lo, hi)], "min_mm": lo, "max_mm": hi}})

    def yield_row(self, row: dict):
        self.rows.append(row)

    def write(self, out_dir):
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{self.stem}.coord.jsonl", "w") as f:
            f.write(json.dumps(self.header, ensure_ascii=False) + "\n")
            for r in self.rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
