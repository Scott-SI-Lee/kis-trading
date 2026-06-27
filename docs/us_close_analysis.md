# us_close_analysis.py 분석 문서

미국 증시 마감 후 한국 증시에 미칠 영향을 분석하여 브리프(Markdown/JSON)를 생성하고, 선택적으로 텔레그램으로 전송하는 비동기 파이프라인.

- 소스 파일: [us_close_analysis.py](us_close_analysis.py)
- 출력 위치: `backend/reports/` (`latest.json`, `latest.md` + 타임스탬프 히스토리)

---

## 1. 개요

| 항목 | 내용 |
|------|------|
| 목적 | 미국 마감 데이터(지수·섹터·종목·매크로) + 뉴스 신호를 종합해 한국 종목 후보를 점수화 |
| 데이터 소스 | Yahoo Finance Chart API, RSS 뉴스 피드(Reuters/Bloomberg/CNBC/Investing/Yahoo) |
| 출력 | Markdown 브리프, JSON 리포트, (옵션) 텔레그램 메시지 |
| 실행 방식 | CLI (`python us_close_analysis.py`), 1회성 또는 매일 정기 스케줄 |
| 핵심 기술 | `asyncio` + `aiohttp` 동시 수집, `dataclass` 기반 모델, 원자적 파일 쓰기 |

파일 상단 docstring에 따르면 기존 구현을 **클래스 기반 구조 / 설정 관리(Config 클래스) / 동시성 최적화 / 에러 처리 강화 / 함수 분리 / 타입 힌팅**으로 리팩토링한 "개선 버전".

---

## 2. 전체 흐름

```
main() (CLI)
  └─ build_us_close_report()
        ├─ [동시] YahooMarketDataClient.fetch_moves()  → 시장 데이터(MarketMove)
        ├─ [동시] RssNewsClient.fetch_recent()          → 뉴스(NewsItem)
        ├─ _news_signal()          → 긍/부정·테마 신호 추출
        ├─ _build_recommendations()→ 종목별 점수·추천 생성
        ├─ _market_summary()       → 시장 요약 불릿
        ├─ _data_quality()         → 수집 신뢰도 메타
        └─ _compose_markdown()     → 최종 Markdown
  └─ save_us_close_report()        → latest/history 파일 저장
  └─ send_telegram_message()       → (옵션) 텔레그램 전송
```

데이터 수집 단계에서 뉴스는 `create_task`로 백그라운드 시작 후 시장 데이터를 `await`하여 **뉴스·시장 동시 수집**한다 ([us_close_analysis.py:1254-1263](us_close_analysis.py#L1254-L1263)).

---

## 3. 데이터 모델 (dataclass)

| 클래스 | 역할 | 비고 |
|--------|------|------|
| `Instrument` | 거래 대상 정의(key/label/symbol/group) | `frozen=True` 불변 |
| `MarketMove` | 시장 움직임 결과(가격·전일·변화율·bp·에러) | 수집 실패 시 `error` 채움 |
| `NewsItem` | 뉴스 1건(소스/제목/링크/발행시각/요약) | |
| `Recommendation` | 종목 추천(방향/점수/근거/리스크) | |
| `TelegramConfig` | 봇 토큰·챗ID·활성화 여부 | |
| `AppConfig` | 전체 설정 집합(`load_default()`) | 의존성 주입 가능하게 분리 |

`group` 값은 `major_index / sector / us_stock / macro` 4종.

---

## 4. 정적 설정 데이터

- **INSTRUMENTS** ([us_close_analysis.py:157-180](us_close_analysis.py#L157-L180)): 22개 종목.
  - 주요지수: S&P500, Nasdaq, Dow, Russell2000
  - 섹터: SOX, XLK, XLE, XLF, XLV
  - 미국주: NVDA, AMD, TSM, MSFT, AAPL, TSLA, AMZN, META
  - 매크로: 미10년물(^TNX), DXY, WTI, 천연가스, 금
- **KOREA_CANDIDATES** ([us_close_analysis.py:190-341](us_close_analysis.py#L190-L341)): 25개 한국 종목. 각 종목은 `tags`(섹터 분류)와 `drivers`(미국 지표 → 가중치 매핑)를 가짐.
  - `drivers`는 **dict(가중치 명시)** 또는 **list(균등 가중)** 둘 다 허용 → `_iter_weighted_drivers()`가 정규화.
- **NEWS_FEEDS**: 환경변수로 override 가능. Reuters는 기본값이 빈 문자열(비활성).
- **POSITIVE_WORDS / NEGATIVE_WORDS**: 뉴스 감성 키워드 사전.

---

## 5. 핵심 로직

### 5.1 시장 데이터 수집 — `YahooMarketDataClient`
- Yahoo Chart API(`range=5d&interval=1d`) 호출 ([us_close_analysis.py:455-515](us_close_analysis.py#L455-L515)).
- 가격: `regularMarketPrice` 우선, 없으면 최근 종가. 전일 종가: `chartPreviousClose` 우선, 없으면 직전 종가.
- `change_pct = (price/prev_close - 1) * 100`.
- **us10y(금리)만** `change_bp`(베이시스포인트) 별도 계산 → 금리는 %가 아닌 bp로 평가.
- 타임아웃/예외는 모두 `_error()` MarketMove로 감싸 **전체 실패를 막음**(graceful degradation).

### 5.2 뉴스 수집 — `RssNewsClient`
- 활성 피드만 동시 호출, `return_exceptions=True`로 개별 피드 실패 격리 ([us_close_analysis.py:539-560](us_close_analysis.py#L539-L560)).
- RSS XML 파싱 → cutoff 시간 내 기사만, 소스별 `limit` 적용.
- 발행일은 `pubDate` 또는 Dublin Core `date` 사용, UTC 정규화.

### 5.3 뉴스 신호 — `_news_signal`
- `_dedupe_news()`로 링크/제목 중복 제거 ([us_close_analysis.py:651-661](us_close_analysis.py#L651-L661)).
- **시간 가중치**: `0.5 ^ (age_hours / 12)` — 반감기 12시간으로 최근 기사 가중 ([us_close_analysis.py:641-648](us_close_analysis.py#L641-L648)).
- 제목 가중치 2.0, 요약 가중치 1.0으로 키워드 매칭(`_keyword_hits`는 단어 경계 정규식 사용).
- 결과: `positive_hits / negative_hits / net_score / themes(상위5) / deduped_news_count`.
- 테마: AI/반도체, 금리/달러, 에너지, 전기차/배터리, 지정학/관세.

### 5.4 점수 산정 — `_build_recommendations`
종목별 점수 = **드라이버 점수 합 + 리스크 페널티 → 섹터 조정**.

1. **드라이버 점수**: 각 미국 지표 변화율을 점수로 변환(`_score_from_pct`, 금리는 `_score_from_bp`) 후 가중합. 점수는 `SCORE_BOUNDS (-4.0~4.0)` 클램프.
2. **리스크 페널티** (`_calculate_risk_penalty`, 전 종목 공통):
   - 금리 bp > 6 → −0.5
   - DXY > 0.4% → −0.4
   - 뉴스 net_score 음수면 추가 페널티, 양수면 부스트
3. **섹터 조정** (`_apply_sector_adjustments`):
   - 반도체: AI/반도체 테마 뉴스 많으면 +0.4
   - 성장주: 금리 상승분만큼 차감, 달러 강세 시 리스크 경고
   - 금융: 금리 상승 시 가산(NIM 기대), 급락 시 경고
   - 에너지: WTI 변화 반영
   - 헬스케어(방어주): 점수가 음수일 때 +0.5 보정
4. **방향**: `score >= 0`이면 up, 아니면 down. 상·하위 각 8개 선별.

### 5.5 시장 요약 — `_market_summary`
주요지수 평균으로 위험선호/회피/혼조 판정, 섹터 리더·래거, 금리·DXY 코멘트 생성.

### 5.6 데이터 품질 — `_data_quality`
시장 수집 성공률, 실패 심볼 목록, 뉴스 건수/소스 수를 리포트에 포함해 신뢰도 점검 가능.

---

## 6. 출력 & 부가 기능

| 기능 | 함수 | 설명 |
|------|------|------|
| Markdown 작성 | `_compose_markdown` | 핵심요약/데이터품질/상승·하락후보/주요데이터/최근뉴스 섹션 |
| 텔레그램 메시지 | `_compose_telegram_message` | 요약본(상·하위 5개씩) |
| 텔레그램 전송 | `send_telegram_message` | 봇 API POST, 타임아웃/에러 처리 |
| 파일 저장 | `save_us_close_report` | `latest.*` + 타임스탬프 히스토리, **원자적 쓰기**(tmp→replace)로 손상 방지 |

---

## 7. 실행 방법 (CLI)

```bash
# 기본 실행 (Markdown 생성 + reports/ 저장)
python us_close_analysis.py

# JSON 출력
python us_close_analysis.py --json

# 특정 파일로 저장
python us_close_analysis.py --output report.md

# 텔레그램 전송 포함
python us_close_analysis.py --telegram

# 매일 KST 06:00 정기 실행
python us_close_analysis.py --schedule
```

| 인자 | 기본값 | 설명 |
|------|--------|------|
| `--hours` | 24 | 뉴스 조회 시간 범위 |
| `--news-per-source` | 10 | 소스별 최대 기사 수 |
| `--json` | off | JSON 출력 |
| `--output` | - | 결과 파일 경로 |
| `--schedule` | off | 정기 실행 모드 |
| `--schedule-hour/minute` | 6 / 0 | 실행 시각(KST) |
| `--output-dir` | reports/ | 저장 디렉토리 |
| `--telegram` | off | 텔레그램 전송 |

### 환경변수
- `US_CLOSE_*_RSS`: 뉴스 피드 URL override
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (또는 `US_CLOSE_*` 변형)
- `US_CLOSE_TELEGRAM_ENABLED`, `US_CLOSE_OUTPUT_DIR`, `US_CLOSE_SCHEDULE_HOUR/MINUTE`

---

## 8. 설계상 강점

- **동시성**: 시장·뉴스 데이터를 `asyncio.gather`/`create_task`로 병렬 수집해 지연 최소화.
- **장애 격리**: 개별 종목·피드 실패가 전체 리포트를 중단시키지 않음(에러를 데이터로 흡수).
- **원자적 파일 쓰기**: `latest.md/json` 동시 읽힘 상황에서 손상 방지.
- **설정 주입**: `AppConfig`로 테스트·커스터마이징 용이.
- **금리 특수 처리**: 금리는 %가 아닌 bp 기준으로 일관 평가.

## 9. 참고/유의점

- 점수 모델은 휴리스틱(가중치·임계값 상수 기반)으로, 통계적 검증보다는 규칙 기반 신호이다.
- Yahoo·RSS는 비공식/무인증 엔드포인트라 차단·스키마 변경에 취약할 수 있다(에러 처리로 완화).
- Reuters 피드는 기본 비활성(빈 URL), 사용 시 환경변수 지정 필요.
- `schedule_us_close_job`은 단순 무한 루프 스케줄러로, 프로세스 상주 가정. 별도 크론/서비스 매니저와의 역할 중복 여부 확인 권장.
