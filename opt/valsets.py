"""검증용 세트 생성 (OPT.Req4 · Req5 · Dec14 · Dec15).

세트 5개 = E1 의 생성기 세트와 같은 설정 (E1 파일 meta.config 에서 읽은 값), 시나리오 수 = E1 과 같음,
시드 = E1 시드 + 20,000,000. 모두 검증용 범위 40000000~49999999.
사용: python -m opt.valsets -o out/val   → 시나리오 JSON + manifest.json (설정 · 시드 · sha256)
"""
import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from . import _sk  # noqa: F401
from pipe_routing.generator import GeneratorConfig, PairConfig, generate, generate_block_pair  # noqa: E402
from pipe_routing.scenario import save  # noqa: E402

SEED_OFFSET = 20_000_000
# (세트, 이름 앞부분, E1 첫 시드, 시나리오 수, 생성 설정) — E1 meta.config 와 같은 값 (OPT.Req5)
SETS = [
    ("base", "val_proc", 20261008, 20, dict(n_pipes=10)),
    ("stair", "val_stair10", 20264008, 5, dict(n_pipes=10, stair_rule=True)),
    ("dense30", "val_dense30", 20262008, 5, dict(n_pipes=30)),
    ("dense50", "val_dense50", 20263008, 5, dict(n_pipes=50)),
    ("pair", "val_pair", 20265008, 5, None),     # PairConfig 기본값 (E1 pair 의 meta.config 와 같음)
]


def make(set_name, prefix, e1_seed, i, kw):
    seed = e1_seed + SEED_OFFSET + i
    assert 40_000_000 <= seed <= 49_999_999
    name = f"{prefix}_{i:03d}"
    if kw is None:
        cfg = PairConfig(seed=seed)
        sc = generate_block_pair(cfg, name)
    else:
        cfg = GeneratorConfig(seed=seed, **kw)
        sc = generate(cfg, name)
    return name, seed, sc, asdict(cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="out/val")
    a = ap.parse_args(argv)
    out = Path(a.out)
    man = {"seed_offset": SEED_OFFSET, "sets": {}}
    for set_name, prefix, s0, n, kw in SETS:
        rows = []
        for i in range(n):
            name, seed, sc, cfg = make(set_name, prefix, s0, i, kw)
            p = out / set_name / f"{name}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            save(sc, p)
            cfg = {k: v for k, v in cfg.items() if k != "sizes"}
            rows.append({"file": str(p.relative_to(out)), "seed": seed, "pipes": len(sc.pipes),
                         "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "config": cfg})
            print(set_name, name, seed, len(sc.pipes), flush=True)
        man["sets"][set_name] = rows
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
