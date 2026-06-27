# 미국증시 마감 분석 로직 개선 작업 (Task)

대상 파일: [backend/us_close_analysis.py](../backend/us_close_analysis.py)
작성일: 2026-06-27
상태 범례: ⬜ 예정 / 🟦 진행중 / ✅ 완료

---

## 🔴 우선순위 높음 (정확성·견고성)

### Task 1. Yahoo 요청 rate limiting + 재시도/백오프 ✅
- **문제**: [us_close_analysis.py:452-453](../backend/us_close_analysis.py#L452-L453) 주석은 "동시 요청 수 제어"라고 하지만 실제로는 `asyncio.gather`로 22개 종목 + 5개 피드 요청이 전부 동시에 발사됨. Yahoo 무인증 엔드포인트는 `429 Too Many Requests`로 차단당하기 쉬움.
- **개선안**:
  - `asyncio.Semaphore(5~8)`로 동시 요청 수 제한
  - 일시적 실패(429, 5xx, timeout) 시 지수 백오프 재시도(예: 2회, 0.5s→1s)
- **영향 함수**: `YahooMarketDataClient.fetch_moves`, `fetch_move`
- **완료 기준**: 동시 요청 수가 세마포어 한도 이하로 제한되고, 429/timeout 시 재시도 후 최종 실패만 `_error`로 기록됨.

### Task 2. 전체 수집 실패 시 "오해 추천" 방지 게이트 ✅
- **문제**: 모든 시장 데이터가 실패하면 드라이버 점수가 0이 되어 `score >= 0` 조건상 전 종목이 "상승 후보"로 분류됨 ([us_close_analysis.py:1269](../backend/us_close_analysis.py#L1269)). `data_quality`는 계산만 하고 게이트로 쓰지 않음.
- **개선안**:
  - `market_success_rate`가 임계값(예: 0.5) 미만이면 리포트에 `low_confidence: true` 마킹
  - 저신뢰 시 텔레그램 전송 스킵(또는 경고 헤더 포함)
  - Markdown 상단에 신뢰도 경고 배너 추가
- **영향 함수**: `build_us_close_report`, `run_us_close_job`, `_compose_markdown`
- **완료 기준**: 인위적으로 전체 실패를 유도했을 때 추천이 비거나 저신뢰로 표시되고 텔레그램이 전송되지 않음.

### Task 3. 시장 개장일·데이터 신선도 검증 ⬜
- **문제**: 스케줄러가 KST 고정 시각에 실행되며 주말·미국 공휴일을 구분하지 않음 ([us_close_analysis.py:1214-1226](../backend/us_close_analysis.py#L1214-L1226)). 휴장일 다음 아침엔 전일과 동일한 stale 데이터로 리포트가 생성됨.
- **개선안**:
  - Yahoo `meta.regularMarketTime`을 `MarketMove`에 보존
  - 마지막 거래 시각이 직전 미국 세션 범위를 벗어나면(너무 오래됨) 신선도 경고/스킵
  - 스케줄러가 주말/휴장 추정 시 실행 스킵 옵션
- **영향 함수**: `fetch_move`(meta 보존), `_data_quality`(신선도 필드), `schedule_us_close_job`
- **완료 기준**: stale 데이터일 때 리포트에 신선도 경고가 표시됨.

---

## 🟡 우선순위 중간 (품질·모델)

### Task 4. 휴리스틱 가중치 외부화 + 적중률 피드백 ✅
- **문제**: 가중치/임계값이 코드 상수로 하드코딩되어 있고, 예측이 실제 한국 종목 다음날 수익률과 맞았는지 평가하는 장치가 없음.
- **개선안**:
  - 가중치/임계값을 외부 설정(YAML/JSON)으로 분리하여 코드 수정 없이 튜닝
  - 다음날 KR 종가와 비교하는 간단한 백테스트/적중률 로그
- **완료 기준**: 설정 파일로 가중치 변경 가능, 적중률 지표 출력.

### Task 5. 금리(us10y) significant-move 판정 단위 정정 ✅
- **문제**: [us_close_analysis.py:837-838](../backend/us_close_analysis.py#L837-L838) — us10y 점수는 bp로 내지만 `abs(pct) >= SIGNIFICANT_MOVE_THRESHOLD(0.5%)` 판정은 pct로 함. 금리 0.5% 변동은 매우 큰 값이라 사실상 reason에 거의 안 잡힘.
- **개선안**: us10y는 bp 기준 임계값(예: 5bp)으로 분기.
- **완료 기준**: 의미 있는 금리 변동이 reason에 정상 노출됨.

### Task 6. 텔레그램 세션 재사용 + 전송 재시도 ⬜
- **문제**: [us_close_analysis.py:1077](../backend/us_close_analysis.py#L1077) — `send_telegram_message`가 매번 자체 `ClientSession`을 열고 실패 시 재시도가 없음.
- **개선안**: 호출부 세션 주입 또는 재시도(1~2회) 추가.

---

## 🟢 우선순위 낮음

### Task 7. 뉴스 크로스소스 중복 제거 보강 ⬜
- 다른 링크의 동일 기사(소스별 중복)는 현재 dedup에서 안 걸림 ([us_close_analysis.py:651](../backend/us_close_analysis.py#L651)). 제목 유사도(정규화/토큰 기반) 보강 검토.

### Task 8. 핵심 순수 함수 단위 테스트 추가 ⬜
- docstring은 "테스트 가능성 개선"을 표방하나 실제 단위 테스트가 없음. `_news_signal`, `_build_recommendations`, `_iter_weighted_drivers`, `_score_from_*`는 순수 함수라 테스트 용이.

---

## 권장 진행 순서

1. **Task 1, 2, 3** (견고성 핵심 — 차단 방지 + 잘못된 신호 방지) ← 우선
2. **Task 5, 6** (작은 수정, 빠른 효과)
3. **Task 4** (모델 튜닝 기반)
4. **Task 7, 8** (점진적 품질 개선)
