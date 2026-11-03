# pipe-routing-opt

섹션 3) APR - Optimization of A* & Support.

walking skeleton(`Navaljey/pipe-routing-skeleton`)의 다중 배관 슬롯에 꽂히는, 학습 없는 최적화 플래너(OPT)를 만든다.
모든 배관의 단독 A* 경로에서 출발해 충돌(관 간섭 · 단자 예약 · 서포트)을 우선순위 탐색으로 풀고, 랙(공유 서포트) 형성으로 J 를 줄여
평가셋 E1 에서 기준선 시간-품질 곡선보다 위에 서는 것이 목표다.

- 사양 · 결정 · 단계: [`CLAUDE.md`](CLAUDE.md)
- 심판: skeleton 커밋 `cd9c13e` 고정 (이 레포에서 수정하지 않는다)
- 이름 규칙: `OPT.Dec` (결정) · `OPT.Req` (요구사항) · `OPT.Stage` (단계) · `OPT.Question` (질문) · `Skeleton.Dec후보` (skeleton 에 올릴 결정)
