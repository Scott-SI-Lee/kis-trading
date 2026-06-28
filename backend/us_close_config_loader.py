"""
미국 마감 분석 가중치 설정 로더
Task 4: 휴리스틱 가중치 외부화
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class USCloseConfig:
    """미국 마감 분석 설정 관리"""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._find_config()
        self.config = self._load_config()

    def _find_config(self) -> str:
        """설정 파일 찾기"""
        possible_paths = [
            Path(__file__).parent.parent / "config" / "us_close_config.yaml",
            Path.cwd() / "config" / "us_close_config.yaml",
            Path.home() / ".kis-trading" / "us_close_config.yaml",
        ]
        for path in possible_paths:
            if path.exists():
                logger.info(f"✅ 설정 파일 로드: {path}")
                return str(path)
        logger.warning("⚠️  설정 파일을 찾을 수 없습니다. 기본값을 사용합니다.")
        return None

    def _load_config(self) -> Dict[str, Any]:
        """YAML 설정 파일 로드"""
        if not self.config_path:
            return self._default_config()

        try:
            import yaml
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
            return config
        except ImportError:
            logger.warning("⚠️  pyyaml이 설치되지 않았습니다.")
            return self._default_config()
        except Exception as e:
            logger.error(f"❌ 설정 파일 로드 실패: {e}")
            return self._default_config()

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        """기본 설정값"""
        return {
            "risk_penalties": {
                "us10y_surge_threshold_pct": 1.0,
                "us10y_surge_penalty": -0.5,
                "dxy_strength_threshold_pct": 0.4,
                "dxy_strength_penalty": -0.4,
                "negative_news_penalty_multiplier": 0.08,
                "negative_news_penalty_cap": -0.8,
                "positive_news_bonus_multiplier": 0.06,
                "positive_news_bonus_cap": 0.6,
            },
            "sector_adjustments": {
                "semiconductor": {
                    "sox_multiplier": 0.7,
                    "ai_news_bonus": 0.4,
                    "ai_news_theme": "AI/반도체",
                },
            },
            "backtest_logging": {
                "enabled": True,
                "log_file": "reports/us_close_backtest.log",
                "evaluation": {
                    "up_recommendation_threshold_pct": 0.5,
                    "down_recommendation_threshold_pct": -0.5,
                    "hold_range": [-0.5, 0.5],
                },
            },
        }

    def get(self, key: str, default: Any = None) -> Any:
        """설정값 조회"""
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def get_risk_penalty_params(self) -> Dict[str, float]:
        """리스크 페널티 파라미터 조회"""
        return self.config.get("risk_penalties", {})

    def get_sector_adjustments(self) -> Dict[str, Dict[str, Any]]:
        """섹터별 조정 파라미터 조회"""
        return self.config.get("sector_adjustments", {})

    def get_backtest_config(self) -> Dict[str, Any]:
        """백테스트 설정 조회"""
        return self.config.get("backtest_logging", {})


class BacktestLogger:
    """미국 마감 분석 백테스트 로거"""

    def __init__(self, log_file: Optional[str] = None):
        cfg = USCloseConfig()
        self.log_file = log_file or cfg.get("backtest_logging.log_file", "reports/us_close_backtest.log")
        self.enabled = cfg.get("backtest_logging.enabled", True)
        self._ensure_log_dir()

    def _ensure_log_dir(self):
        """로그 디렉토리 생성"""
        log_path = Path(self.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_recommendation(
        self,
        symbol: str,
        name: str,
        direction: str,
        score: float,
        reasons: List[str],
        actual_change_next_day: Optional[float] = None,
        accuracy: Optional[str] = None,
    ):
        """추천 결과 로깅"""
        if not self.enabled:
            return

        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "name": name,
                "direction": direction,
                "score": round(score, 2),
                "reasons": reasons[:2],
                "actual_change_next_day": actual_change_next_day,
                "accuracy": accuracy or "unknown",
            }

            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

            logger.debug(f"✅ 백테스트 로그: {symbol} {direction}")
        except Exception as e:
            logger.error(f"❌ 백테스트 로그 실패: {e}")

    def evaluate_recommendation(
        self,
        direction: str,
        actual_change_pct: float,
        cfg: Optional[USCloseConfig] = None,
    ) -> str:
        """추천 정확도 평가"""
        if actual_change_pct is None:
            return "unknown"

        cfg = cfg or USCloseConfig()
        eval_cfg = cfg.get("backtest_logging.evaluation", {})

        up_threshold = eval_cfg.get("up_recommendation_threshold_pct", 0.5)
        down_threshold = eval_cfg.get("down_recommendation_threshold_pct", -0.5)

        if direction == "up":
            return "correct" if actual_change_pct >= up_threshold else "incorrect"
        elif direction == "down":
            return "correct" if actual_change_pct <= down_threshold else "incorrect"
        else:
            return "unknown"

    def generate_report(self) -> Dict[str, Any]:
        """백테스트 리포트 생성"""
        if not Path(self.log_file).exists():
            return {"message": "로그 파일이 없습니다"}

        stats = {
            "total": 0,
            "correct": 0,
            "incorrect": 0,
            "unknown": 0,
        }

        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        accuracy = entry.get("accuracy", "unknown")
                        stats["total"] += 1
                        stats[accuracy] += 1
                    except json.JSONDecodeError:
                        continue

            if stats["total"] > 0:
                stats["accuracy_pct"] = round(
                    stats["correct"] / (stats["correct"] + stats["incorrect"]) * 100
                    if (stats["correct"] + stats["incorrect"]) > 0
                    else 0,
                    2,
                )

            return stats
        except Exception as e:
            return {"error": str(e)}


_global_config: Optional[USCloseConfig] = None


def get_config() -> USCloseConfig:
    """전역 설정 인스턴스 조회"""
    global _global_config
    if _global_config is None:
        _global_config = USCloseConfig()
    return _global_config


if __name__ == "__main__":
    cfg = get_config()
    print("✅ 설정 로드 완료")
