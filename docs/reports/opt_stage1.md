# OPT.Stage1 보고서 — 1차 (레포 구성 · 심판 일치 확인 · 미결 조사)

**작성일:** 2026-11-03
**상태:** **중간 보고 — 측정 전 멈춤.** 루트 구현 · 충돌 검출 4종 · 검증용 시드 측정은 하지 않았다.
멈춘 이유: ① 서포트 표 sha256 확인 불가 (OPT.Req9, CLAUDE.md §10-3) ② 사용자 지시 3 — 두 미결 조사 보고를 먼저 승인받는다.

---

## 1. 실행 조건

| 항목 | 값 |
|---|---|
| OPT 코드 커밋 | 이 PR 의 브랜치 `claude/determined-faraday-p8jpxv` 머리 커밋 |
| skeleton 커밋 | `cd9c13e527935c43d79e6e60895260264617adba` (서브모듈 `skeleton/`, 수정 없음) |
| 서포트 표 | **공개 임시 표** (`public_approx`, sha256 `99babf41…`) — 비공개 표 없음, §2.3 |
| 머신 | Intel Xeon Processor · 4 코어 · 메모리 15 GB · Python 3.13.18 · numpy 2.5.3 · numba 0.68.0 |
| 병렬 수 · 동시 실행 수 | 1 · 1 (조사용 실행만) |
| 시드 | 검증용 `40000000` 1개 (§3.1 인터페이스 확인용, 기본 세트 설정) |

E1 측정 머신과 Python 버전이 다르다 (E1 = 3.13.16 · Xeon @ 2.80 GHz). 기준선 비교 측정은 D68 대로 이 머신에서 기준선을 다시 잰다.

## 2. 레포 구성 · 심판 일치 확인

### 2.1 레포 구성 (OPT.Dec9)

- `skeleton/` = git 서브모듈 `Navaljey/pipe-routing-skeleton` @ `cd9c13e` (고정). OPT 코드는 `opt/`.
- skeleton 은 설치하지 않고 `sys.path` 에 `skeleton/` 을 넣어 import 한다 (패키지 설치 정의가 skeleton 에 없다).
- 클론: `git clone --recurse-submodules …` 또는 `git submodule update --init`.

### 2.2 확인 도구

`python -m opt.judge_check` — ① 서브모듈 커밋 ② E1 시나리오 41개 sha256 ③ 서포트 표 source · sha256. 하나라도 다르면 종료 코드 1.

### 2.3 결과

| 항목 | 기대 | 실제 | 판정 |
|---|---|---|---|
| skeleton 커밋 | `cd9c13e…` | `cd9c13e…` | 일치 |
| E1 시나리오 sha256 | 41개, `docs/reports/s1_8_scenario_sha256.json` | 41/41 일치 | 일치 |
| 서포트 표 | `private`, `ac2ba6a2…cffb3` | `public_approx`, `99babf41…` | **불일치 — 멈춤 (§10-3)** |

- 이 세션에는 비공개 서포트 표가 없다 (`PIPE_SUPPORT_STANDARD` 미설정). 비공개 레포를 세션에 붙이는 요청은 **이 세션의 권한 설정에서 거부**됐다 → OPT.Question1.
- 공개 임시 표로 잰 결과는 비교에 쓰지 않는다. §3.1 의 실행은 인터페이스 동작 확인이며 수치는 측정값이 아니다.

### 2.4 참고 — `docs/eval_set_E1.md` 위치

`docs/eval_set_E1.md` · `docs/eval_set_E1_baseline.json` 은 `cd9c13e` 에 **없고**, 그 뒤 skeleton main 커밋 `1cdd57b` (S1-8 E1 동결 문서) 에 있다.
`cd9c13e..1cdd57b` 차이를 확인했다: `pipe_routing/` · `schemas/` · `scenarios/` · `tests/` 변경 **0**, 문서 · 그림 · `r7_cases` 만 바뀜.
따라서 코드 고정은 `cd9c13e` 그대로 두고, E1 기준선 수치는 `1cdd57b` 의 문서를 읽기로만 참조한다 (`git -C skeleton show 1cdd57b:docs/eval_set_E1.md`). 문제가 아니라 기록용이다.

## 3. 미결 조사

### 3.1 §7.2 — 단자 예약은 지키고 놓인 배관은 없는 단독 라우팅

**결론: skeleton 수정 없이 가능하다. 기준선 라우터를 `placed=()` 로 부르면 된다.**

```python
from pipe_routing.layered import layered_router_a
r = layered_router_a(sc, pipe, time_limit, placed=())
```

근거 (`skeleton/pipe_routing/layered.py` `make_layered_router.router`, 1259~1268행):

| 단계 | 코드 | `placed=()` 일 때 |
|---|---|---|
| 단자 예약 (D59) | `terminal_reservations(sc, exclude={pipe.id} ∪ {placed id})` | 자기 자신만 빠짐 → **다른 모든 배관의 양 단자 예약** 2·(N−1) 개 |
| 부재 예약 (D90) | `SupportMembers(sc, placed)` | `placed` 가 비면 빈 목록, Session 을 건드리지 않음 |
| 확장 상한 | `default_budget(sc)` | 2²² (블록 쌍 2²³) — OPT.Req7 과 같음 |

D101 단독 (예약 없음) 은 `layered_router_a.without_reservations` 로, OPT.Dec6 루트와 다른 호출이다.

**동작 확인** (`python -m opt.probes.stage1_solo_reservation_probe`, 검증용 시드 40000000, 기본 세트 설정, 공개 임시 표):

| 확인 | 결과 |
|---|---|
| 배관마다 예약 대상 수 | 18 = 2 × (10 − 1) |
| 부재 예약 (`graph_stats.support_members`) | 10배관 모두 0 |
| 경로 ↔ 다른 배관 예약 구간 간섭 (`multi.routes_conflict`, 검증기와 같은 식) | 경로 있는 9배관 모두 0 |
| 예약 없는 단독 (D101 방식) 과 경로가 다른 배관 | 1/10 (P001 15A) — 예약 없는 경로는 P005 · P007 예약을 침범, 예약 있는 경로는 침범 없음 |
| 경로 없음 | P005 500A unreachable (확장 121) — 예약 유무와 무관 |

**우선순위 탐색 재라우팅과의 관계 (§7.4, 참고):** 같은 라우터를 `placed` = 우선 배관 경로로 부르면 예약은 `placed` 에 없는 배관(우선순위가 낮거나 관계없는 배관) 단자에만 남는다 — OPT.Dec6 와 맞는다. 다만 이때 **D90 부재 예약이 자동으로 켜진다** (놓인 배관의 서포트 부재를 피함) → OPT.Question3.

### 3.2 §7.3 — 검증기가 충돌 4 (서포트 부재와의 간섭) 를 판정하는가

**결론: 판정한다. 다만 독립된 위반이 아니라 "p 의 서포트 배치" 안에서 판정되어, 충돌 4 는 출력에 충돌 3 (p 의 support 위반) 또는 p 의 서포트 재배치(J 변화) 로만 나타난다. 심판 문제로 볼 사항은 없다.**

근거:

1. **부재는 다른 배관을 피해서 놓인다.** 검증기 support 모듈은 서포트 후보 위치마다 `support_std.evaluate(..., others=(얹히지 않은 모든 배관 조각), exact=True)` 를 부르고 (`verifier/modules.py` `support_candidates`, 370~400행), `support_std.members_blocked` 가 부재(앵글 단면 막대) ↔ 얹히지 않은 배관을 `거리 − slack < r_o + 반폭` 으로 판정한다 (275행~, D80 ⑧). 막히면 그 (면, 형식) 조합은 후보에서 빠진다.
2. **D 묶음 · D89 함께 받치기도 같다.** `verifier/support_group.py` 81~88행에서 가로대 · 다리 ↔ 얹히지 않은 배관을 같은 함수로 판정한다 (D82 "간섭 없음").
3. **collision 모듈은 부재를 보지 않는다** (`modules.py` 69행: 직관 ↔ 장애물 · 다른 배관만). 부재 ↔ 배관은 1 · 2 의 support 판정에서만 본다.

따라서 검증기 출력에는 "q 의 배관이 p 의 부재를 관통한 채 둘 다 통과" 상태가 생길 수 없다. q 가 p 의 부재 자리를 지나가면:

| p 쪽 결과 | Layer 0 | J |
|---|---|---|
| 다른 (면, 형식) · 다른 위치로 서포트 이동 가능 | 통과 | p 의 서포트 무게가 바뀜 (보통 증가) |
| 최대 간격 안에 가능 위치 없음 (D89 로도 안 됨) | **p support 위반** = 충돌 3 (부재 막힘, R7) | p 실패 |

즉 §7.3 표의 충돌 3 과 충돌 4 는 **같은 사건을 막힌 쪽(p)과 막은 쪽(q) 에서 본 것** 이고, 검증기는 이를 p 의 support 판정 하나로 처리한다.
CLAUDE.md §7.3 은 "검증기가 판정하지 않으면 심판 문제로 보고" 라고 했는데, 판정은 하므로 심판 문제가 아니다. 다만 충돌 4 를 충돌 3 과 따로 세는 방식은 문서에 없는 규칙이 필요하다 → OPT.Question2.

## 4. 결과 표 (세트별)

없음 — 측정 전 멈춤 (§2.3).

## 5. 이번 단계에서 생긴 `미결:` · `추정:` · 확인된 사항

| 항목 | 상태 |
|---|---|
| §7.2 `미결:` | **해소 제안** — `layered_router_a(sc, pipe, t, placed=())` (§3.1). 승인 대기 |
| §7.3 `미결:` | **해소 제안** — 검증기가 판정함, 충돌 4 는 충돌 3 · 서포트 재배치로 나타남 (§3.2). 정의는 OPT.Question2 |
| `추정:` (새로) | 없음 |

## 6. 심판 문제로 보고할 사항

없음.

## 7. 다음 진행에 필요한 결정 (OPT.Question)

| 번호 | 질문 | 제안 |
|---|---|---|
| **OPT.Question1** | 비공개 서포트 표를 이 세션에서 어떻게 쓰는가. 비공개 레포를 세션에 붙이는 요청이 권한 설정에서 거부됐다. 표 없이는 OPT.Req9 를 통과하지 못해 측정을 시작할 수 없다 | 소유자가 이 세션(또는 환경)에 비공개 표 레포 읽기 권한을 허용 → 레포 밖 경로에 받아 `PIPE_SUPPORT_STANDARD` 로 지정. OPT 레포(공개)에는 표 파일 · 수치를 넣지 않는다 (D78) |
| **OPT.Question2** | 충돌 4 를 어떻게 정의 · 검출하는가 (§3.2) | (가) 충돌 4 를 충돌 3 에 합친다 — "단독 검증 support 통과 ∧ 다중 검증 support 위반" 배관 p 와, p 의 막힌 부재 영역을 지나는 배관 q 를 책임 쌍으로 등록 (skeleton `support_diag` 방식). (나) 위반 없이 p 의 서포트가 무거워지는 경우는 충돌이 아니라 §7.7 채점(J)에서만 다룬다 |
| **OPT.Question3** | 우선순위 탐색 재라우팅에서 D90 부재 예약을 켜는가. 기준선 라우터 기본값은 켜짐이고, 켜면 충돌 3 의 "(가) 나중 배관이 막음" 유형이 재라우팅 단계에서 미리 피해진다 | 켠다 (기준선 라우터를 그대로 씀, CLAUDE.md §6 "수정 없이 호출만") |
| **OPT.Question4** | 수작업 세트의 검증용 대응물. 수작업 시나리오는 생성기 설정이 없어 (`meta.config` 없음) OPT.Req5 를 적용할 수 없다 | 검증용 측정은 생성기 5 세트 (기본 · 계단 · 밀집 30 · 밀집 50 · 블록 쌍) 만. 수작업은 OPT.Stage4 E1 측정에만 |
| **OPT.Question5** | 검증용 세트의 시나리오 수와 시드 배치 (문서에 없음) | 세트별 시나리오 수 = E1 과 같음 (기본 20 · 계단 5 · 밀집 30 5 · 밀집 50 5 · 블록 쌍 5). 시드 = 기본 40261008~ · 밀집 30 40262008~ · 밀집 50 40263008~ · 계단 40264008~ · 블록 쌍 40265008~ (E1 시드 + 20,000,000, 세트끼리 겹치지 않음) |
| **OPT.Question6** | 루트 시간의 "병렬" 측정 병렬 수 | 4 (이 머신 코어 수) — 순차(1) 와 함께. OPT.Dec10 대로 병렬 값은 참고 |

**생성기 설정 (OPT.Req5, 확인만):** E1 파일의 `meta.config` 에서 읽었다 — 기본 · 밀집 = `GeneratorConfig` 기본값 (채움 0.12 · 중력관 0.3 · 노즐 0.5 · 데크 0.5 · 맨해튼 ≥ 5,000 mm), 배관 10 / 30 / 50, `stair_rule` · `d94_rule` 없음(= False). 계단 = 10배관 + `stair_rule = True`. 블록 쌍 = `PairConfig` 기본값 (블록당 10 + 관통 4, 창 4 m × 2 m, `stair_rule` · `d94_rule` True). 검증용 시나리오는 이 설정에 시드만 바꿔 만든다.

## 8. 이번 PR 의 파일

| 파일 | 내용 |
|---|---|
| `.gitmodules`, `skeleton` | skeleton 서브모듈 @ `cd9c13e` |
| `opt/judge_check.py` | OPT.Req9 심판 일치 확인 |
| `opt/probes/stage1_solo_reservation_probe.py` | §7.2 조사 (검증용 시드만 받음) |
| `docs/reports/opt_stage1.md` | 이 보고서 |
