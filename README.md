# 📈 KIS 자동매매 시스템

한국투자증권 OpenAPI를 활용한 웹 기반 주식 자동매매 대시보드입니다.
실시간 시세 조회, 조건 기반 자동매매, 종목 스크리너 기능을 제공합니다.

> ⚠️ **주의:** 이 프로젝트는 교육 목적으로 제작되었습니다. 실제 투자 손실에 대한 책임은 사용자에게 있으며, 반드시 **모의투자로 먼저 테스트**하세요.

---

## 📁 프로젝트 구조

```
kis_trading/
├── backend/
│   ├── main.py              # FastAPI 서버 (REST API + WebSocket)
│   ├── kis_api.py           # 한국투자증권 OpenAPI 래퍼
│   ├── strategy.py          # 자동매매 전략 (골든크로스, RSI, 볼린저밴드, MACD)
│   ├── screener.py          # 종목 스크리너
│   ├── requirements.txt     # Python 패키지 목록
│   ├── .env.example         # 환경변수 템플릿 (기본)
│   ├── .env.local.example   # 환경변수 템플릿 (로컬 개발용)
│   └── .env.prod.example    # 환경변수 템플릿 (프로덕션)
├── frontend/
│   └── index.html           # 웹 대시보드 (단일 파일)
├── .gitignore
└── README.md
```

---

## 🚀 시작하기

### 1. API 키 발급

[KIS Developer Portal](https://apiportal.koreainvestment.com) 에 접속하여 앱을 등록하고 키를 발급받습니다.

- 모의투자와 실전투자 앱을 **각각 별도로** 등록해야 합니다
- 발급된 `APP KEY`와 `APP SECRET`을 복사해 둡니다

### 2. 패키지 설치

```bash
cd kis_trading/backend
pip3 install -r requirements.txt
```

### 3. 환경변수 설정

프로파일에 맞는 템플릿을 복사해서 실제 값을 입력합니다.

```bash
# 기본 (모의 + 실전 모두 설정)
cp .env.example .env

# 로컬 개발용 (모의만 사용)
cp .env.local.example .env.local

# 프로덕션 (실전 거래)
cp .env.prod.example .env.prod
```

`.env` 파일 형식:

```env
# 모의투자 계좌
KIS_MOCK_APP_KEY=여기에_모의투자_APP_KEY
KIS_MOCK_APP_SECRET=여기에_모의투자_APP_SECRET
KIS_MOCK_ACCOUNT_NO=50123456-01

# 실전투자 계좌
KIS_REAL_APP_KEY=여기에_실전투자_APP_KEY
KIS_REAL_APP_SECRET=여기에_실전투자_APP_SECRET
KIS_REAL_ACCOUNT_NO=50123456-01
```

### 4. 백엔드 서버 실행

```bash
# 기본 (.env 로드)
python3 main.py

# 로컬 개발 (.env.local 로드)
python3 main.py --env local

# 프로덕션 (.env.prod 로드)
python3 main.py --env prod
```

서버가 정상 실행되면 아래처럼 출력됩니다:

```
🌿 환경 프로파일: [local] (.env.local)
✅ .env 자동 인증 완료 (모의투자 / 50123456-01)
🚀 서버 시작 | 프로파일: [local] | http://0.0.0.0:8000
```

### 5. 웹 대시보드 열기

```bash
cd ../frontend
python3 -m http.server 3000
```

브라우저에서 [http://localhost:3000](http://localhost:3000) 접속

---

## 🖥️ 주요 기능

### 대시보드
- 총 평가금액, 예수금, 매입금액, 자동매매 상태 한눈에 확인
- 보유 종목 수익률 실시간 표시
- 전략 실행 로그 스트림

### 시세 조회
- 종목코드 입력 시 현재가, 등락률, 거래량 조회
- 이동평균선(단기/장기)이 오버레이된 일봉 차트

### 수동 주문
- 시장가 / 지정가 매수·매도 주문
- 주문 내역 조회

### 자동매매 전략

| 전략 | 매수 신호 | 매도 신호 |
|------|----------|----------|
| **골든크로스** | 단기MA가 장기MA 상향 돌파 | 단기MA가 장기MA 하향 돌파 |
| **RSI** | RSI ≤ 30 (과매도) | RSI ≥ 70 (과매수) |
| **볼린저밴드** | 종가가 하단밴드 이탈 | 종가가 상단밴드 돌파 |
| **MACD** | MACD가 Signal 상향 돌파 | MACD가 Signal 하향 돌파 |

- 포지션 없을 때만 매수, 보유 중일 때만 매도 (중복 주문 방지)
- WebSocket으로 매매 신호 실시간 알림
- 전략 로그 최대 200건 보관

### 종목 스크리너
- KOSPI200 + KOSDAQ150 대상 (약 350종목)
- RSI, 볼린저밴드, MACD, 골든크로스, 거래량 급증, 등락률 조건 복수 적용
- 실시간 진행률 표시 및 결과 테이블
- 조건 충족 종목에서 바로 차트 조회 가능

### 장중 급등주 AI 엔진
- 1분봉 기준으로 향후 15분 내 현재가 대비 +2% 도달 여부를 Binary Classification으로 학습
- 거래대금/거래량 급증, VWAP 괴리, RSI, MACD, 볼린저밴드, 신고가 돌파, 시장 상황, 호가 불균형 피처 반영
- TimeSeriesSplit 기반 Walk Forward Validation으로 미래 데이터 누수와 랜덤 셔플 방지
- Optuna 목표를 Accuracy가 아닌 Profit Factor, Sharpe Ratio, MDD, 승률 조합으로 최적화
- AI 확률에 거래대금 점수와 돌파 점수를 곱해 최종 점수를 만들고 저장 모델 보유 종목을 실시간 랭킹
- 익절/손절, 트레일링 스탑, 15분 시간청산 전략별 백테스트 결과 비교

### 미국 마감 후 한국 증시 브리프
- S&P500, Nasdaq, Dow, Russell2000, SOX, XLK/XLE/XLF/XLV, 주요 미국 빅테크, 금리/달러/원자재 데이터를 Yahoo Finance에서 수집
- Reuters, Bloomberg, CNBC, Investing, Yahoo Finance RSS에서 최근 24시간 뉴스만 수집
- 미국 마감 데이터와 뉴스 키워드를 종합해 한국 증시 상승 후보와 하락 위험 후보를 간결한 근거와 함께 제시
- 서버 없이 CLI로 실행 가능
- Reuters RSS는 환경에 따라 기본값이 비어 있을 수 있으니, 필요하면 `US_CLOSE_REUTERS_RSS`로 직접 지정하세요
- FastAPI 서버를 띄우면 오전 6시 KST 자동 실행 스케줄러도 함께 시작됩니다
- 결과 파일은 기본적으로 `backend/reports/latest.json`와 `backend/reports/latest.md`에 저장됩니다
- 텔레그램 전송은 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 설정하고 `US_CLOSE_TELEGRAM_ENABLED=true`로 켤 수 있습니다

```bash
cd backend
python3 us_close_analysis.py --hours 24
python3 us_close_analysis.py --json --output ../us-close-report.json
python3 us_close_analysis.py --schedule
python3 us_close_analysis.py --telegram
```

---

## 📡 API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/auth` | 수동 인증 (키 직접 입력) |
| `GET` | `/api/env-status` | .env 설정 상태 및 프로파일 조회 |
| `POST` | `/api/switch-mode` | 모의 ↔ 실전 계좌 전환 |
| `GET` | `/api/price/{symbol}` | 현재가 조회 |
| `GET` | `/api/price/{symbol}/history` | 일봉 OHLCV |
| `GET` | `/api/balance` | 계좌 잔고 |
| `GET` | `/api/positions` | 보유 종목 |
| `POST` | `/api/order` | 매수/매도 주문 |
| `GET` | `/api/orders` | 주문 내역 |
| `POST` | `/api/strategy/start` | 자동매매 시작 |
| `POST` | `/api/strategy/stop` | 자동매매 중지 |
| `GET` | `/api/strategy/status` | 전략 상태 |
| `GET` | `/api/strategy/log` | 전략 실행 로그 |
| `POST` | `/api/screener/run` | 종목 스크리닝 시작 |
| `POST` | `/api/screener/stop` | 스크리닝 중지 |
| `GET` | `/api/screener/progress` | 스크리닝 진행률 |
| `GET` | `/api/screener/result` | 스크리닝 결과 |
| `POST` | `/api/intraday-ai/train` | 장중 급등주 AI 모델 학습 |
| `GET` | `/api/intraday-ai/models` | 저장된 장중 AI 모델 목록 |
| `GET` | `/api/intraday-ai/predict/{symbol}` | 저장된 장중 AI 모델로 최신 1분봉 스코어링 |
| `POST` | `/api/intraday-ai/rank` | 저장 모델 보유 종목 실시간 랭킹 |
| `GET` | `/api/intraday-ai/rank/result` | 최근 장중 AI 랭킹 결과 |
| `GET` | `/api/us-close-analysis` | 미국 마감 데이터/최근 뉴스 기반 한국 증시 브리프 |
| `WS` | `/ws` | 실시간 알림 WebSocket |
| `WS` | `/ws/price/{symbol}` | 실시간 시세 WebSocket |

API 문서는 서버 실행 후 [http://localhost:8000/docs](http://localhost:8000/docs) 에서 자동 생성됩니다.

---

## 🌿 환경 프로파일

| 실행 명령 | 로드 파일 | 용도 |
|----------|----------|------|
| `python3 main.py` | `.env` | 기본 |
| `python3 main.py --env local` | `.env.local` | 로컬 개발 |
| `python3 main.py --env dev` | `.env.dev` | 개발 서버 |
| `python3 main.py --env prod` | `.env.prod` | 프로덕션 |
| `python3 main.py --env staging` | `.env.staging` | 임의 이름 가능 |

현재 로드된 프로파일은 대시보드 헤더 우측 `ENV: local` 뱃지로 확인할 수 있습니다.

---

## 🔒 보안

- `.env`, `.env.local`, `.env.prod` 등 실제 키가 담긴 파일은 `.gitignore`에 등록되어 있어 깃허브에 올라가지 않습니다
- `.env.*.example` 파일만 커밋되며, 키값은 포함되지 않습니다
- API 키를 실수로 커밋했다면 즉시 [KIS Developer Portal](https://apiportal.koreainvestment.com)에서 재발급하세요

---

## ⚙️ 기술 스택

| 영역 | 기술 |
|------|------|
| 백엔드 | Python 3.10+, FastAPI, uvicorn, aiohttp |
| 실시간 통신 | WebSocket |
| 환경변수 | python-dotenv |
| 프론트엔드 | Vanilla JS, Chart.js, HTML/CSS (단일 파일) |
| 대상 API | 한국투자증권 OpenAPI (REST) |

---

## 📜 라이선스

이 프로젝트는 교육 및 개인 사용 목적으로 제작되었습니다.
한국투자증권 OpenAPI 이용약관을 반드시 준수하세요.
