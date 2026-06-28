# Task 4: 휴리스틱 가중치 외부화 + 적중률 피드백

## 📋 구현 내용

### 1. 설정 파일 분리
- **파일**: `config/us_close_config.yaml`
- **목적**: 모든 가중치/임계값을 코드에서 분리
- **장점**: 코드 수정 없이 파라미터 튜닝 가능

### 2. 설정 로더 모듈
- **파일**: `backend/us_close_config_loader.py`
- **클래스**: 
  - `USCloseConfig`: 설정 로드 및 조회
  - `BacktestLogger`: 백테스트 로깅 및 평가

### 3. us_close_analysis.py 통합
- `_build_recommendations()`: 설정에서 가중치 로드
- `build_us_close_report()`: 백테스트 로깅 추가
- 모든 하드코딩된 값을 외부 설정으로 변경

## 🔧 사용 방법

### 1. 설정 변경
```yaml
# config/us_close_config.yaml
risk_penalties:
  us10y_surge_threshold_pct: 1.0    # 조정 가능
  us10y_surge_penalty: -0.5         # 조정 가능
```

### 2. 백테스트 로그 확인
```python
from backend.us_close_config_loader import BacktestLogger

logger = BacktestLogger()
report = logger.generate_report()
# {
#   "total": 100,
#   "correct": 65,
#   "incorrect": 35,
#   "accuracy_pct": 65.0
# }
```

### 3. 권장 값 조회
```python
cfg = get_config()
risk_cfg = cfg.get_risk_penalty_params()
print(risk_cfg)
```

## 📊 백테스트 로그 형식

`reports/us_close_backtest.log` (JSON Lines)

```json
{"timestamp": "2026-06-27T14:30:00", "symbol": "005930", "name": "삼성전자", "direction": "up", "score": 2.5, "reasons": ["SOX 강세", "AI 뉴스"], "actual_change_next_day": 1.2, "accuracy": "correct"}
```

## ✅ Task 4 완료 기준

- [x] 설정 파일로 가중치 변경 가능
- [x] 적중률 지표 로그 출력
- [x] 다음날 KR 종가와 비교 가능한 구조

## 🚀 다음 단계

1. **Task 5**: 금리 unit 정정
2. **Task 6**: 텔레그램 세션 재사용
3. **Task 7**: 뉴스 중복 제거
4. **Task 8**: 단위 테스트

## 📝 변경 이력

- 2026-06-27: Task 4 구현 완료
  - config/us_close_config.yaml 추가
  - backend/us_close_config_loader.py 추가
  - backend/us_close_analysis.py 통합
