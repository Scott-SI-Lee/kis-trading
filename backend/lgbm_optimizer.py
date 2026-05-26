"""
lgbm_optimizer.py

전체 파이프라인:
  1. KIS API로 일봉 수집
  2. 피처 생성 (feature_engineering.py)
  3. Optuna로 LightGBM 하이퍼파라미터 탐색
  4. 최적 모델로 백테스트 (수수료 0.015% 반영)
  5. 결과 반환 → 대시보드에 표시

레이블: horizon일 후 수익률로 BUY(1) / SELL(2) / HOLD(0) 분류
"""

import asyncio
import logging
import json
import os
from datetime import datetime
from typing import Optional, Callable, List, Dict
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
import optuna
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import f1_score, classification_report
from sklearn.preprocessing import LabelEncoder

from feature_engineering import build_features, make_labels, FEATURE_COLS

try:
    import xgboost as xgb
except ImportError:
    xgb = None

optuna.logging.set_verbosity(optuna.logging.WARNING)
logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)


def _json_safe(value):
    """json.dumps가 처리하지 못하는 numpy/pandas 값을 기본 Python 타입으로 변환"""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


# ════════════════════════════════════════════════════════════
# 백테스트 엔진
# ════════════════════════════════════════════════════════════
def backtest(df: pd.DataFrame, preds: np.ndarray,
             commission: float = 0.00015) -> dict:
    """
    예측 시그널로 간단한 백테스트 수행
    - BUY(1)  → 다음날 시가 매수
    - SELL(2) → 다음날 시가 매도
    - 수수료 commission (기본 0.015%)
    """
    closes = df["close"].values
    opens  = df["open"].values if "open" in df.columns else closes
    n = len(preds)

    cash     = 1_000_000   # 초기 자본 100만원
    position = 0           # 보유 수량
    equity_curve = [cash]
    trades = []
    buy_price = 0.0

    for i in range(n - 1):
        price_tomorrow = opens[i + 1] if i + 1 < len(opens) else closes[i]
        signal = preds[i]

        if signal == 1 and position == 0 and cash > 0:          # 매수
            qty = int(cash / (price_tomorrow * (1 + commission)))
            if qty > 0:
                cost = qty * price_tomorrow * (1 + commission)
                cash -= cost
                position = qty
                buy_price = price_tomorrow
                trades.append({"type":"BUY","price":price_tomorrow,"qty":qty,"date":df["date"].iloc[i+1] if "date" in df.columns else i+1})

        elif signal == 2 and position > 0:                       # 매도
            revenue = position * price_tomorrow * (1 - commission)
            pnl_pct = (price_tomorrow - buy_price) / buy_price * 100
            cash += revenue
            trades.append({"type":"SELL","price":price_tomorrow,"qty":position,"pnl_pct":round(pnl_pct,2),"date":df["date"].iloc[i+1] if "date" in df.columns else i+1})
            position = 0

        total = cash + position * closes[i]
        equity_curve.append(total)

    # 포지션 미청산 시 마지막 가격으로 정산
    final_value = cash + position * closes[-1]
    total_return = (final_value - 1_000_000) / 1_000_000 * 100

    # MDD 계산
    curve = np.array(equity_curve)
    peak  = np.maximum.accumulate(curve)
    dd    = (curve - peak) / peak * 100
    mdd   = float(dd.min())

    # 승률
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    win_rate = (
        len([t for t in sell_trades if t.get("pnl_pct", 0) > 0]) / len(sell_trades) * 100
        if sell_trades else 0
    )

    # 샤프비율 (일간 수익률 기준)
    daily_returns = pd.Series(curve).pct_change().dropna()
    sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

    return {
        "total_return":  round(total_return, 2),
        "final_value":   round(final_value),
        "mdd":           round(mdd, 2),
        "win_rate":      round(win_rate, 2),
        "sharpe":        round(sharpe, 3),
        "num_trades":    len(sell_trades),
        "equity_curve":  [round(v) for v in equity_curve[::5]],   # 5일 간격으로 압축
        "trades":        trades[-30:],    # 최근 30건
    }


# ════════════════════════════════════════════════════════════
# Optuna + LightGBM 최적화
# ════════════════════════════════════════════════════════════
def _objective(trial: optuna.Trial, X: pd.DataFrame, y: pd.Series,
               tscv: TimeSeriesSplit) -> float:
    """Optuna objective: TimeSeriesSplit CV의 평균 F1 최대화"""
    params = {
        "objective":       "multiclass",
        "num_class":       3,
        "metric":          "multi_logloss",
        "verbosity":       -1,
        "boosting_type":   "gbdt",
        "n_estimators":    trial.suggest_int("n_estimators", 100, 800),
        "learning_rate":   trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "num_leaves":      trial.suggest_int("num_leaves", 16, 128),
        "max_depth":       trial.suggest_int("max_depth", 3, 10),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 80),
        "subsample":       trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha":       trial.suggest_float("reg_alpha", 1e-8, 1.0, log=True),
        "reg_lambda":      trial.suggest_float("reg_lambda", 1e-8, 1.0, log=True),
    }

    # 레이블 인코딩
    horizon     = trial.suggest_int("horizon", 3, 15)
    buy_thr     = trial.suggest_float("buy_threshold", 0.01, 0.05)
    sell_thr    = trial.suggest_float("sell_threshold", -0.05, -0.005)

    scores = []
    for train_idx, val_idx in tscv.split(X):
        X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
        if len(y_tr.unique()) < 2:
            continue
        model = lgb.LGBMClassifier(**params)
        model.fit(X_tr, y_tr,
                  eval_set=[(X_val, y_val)],
                  callbacks=[lgb.early_stopping(50, verbose=False),
                              lgb.log_evaluation(-1)])
        preds = model.predict(X_val)
        score = f1_score(y_val, preds, average="macro", zero_division=0)
        scores.append(score)

    return float(np.mean(scores)) if scores else 0.0


def _cv_macro_f1(model_factory: Callable, X: pd.DataFrame, y: pd.Series,
                 tscv: TimeSeriesSplit, encode_labels: bool = False) -> dict:
    """TimeSeriesSplit 교차검증으로 fold별/평균 macro F1 계산"""
    scores = []
    skipped = 0
    for tr_idx, val_idx in tscv.split(X):
        X_tr, X_val = X.iloc[tr_idx], X.iloc[val_idx]
        y_tr, y_val = y.iloc[tr_idx], y.iloc[val_idx]

        if len(y_tr.unique()) < 2:
            skipped += 1
            continue
        if set(y_val.unique()) - set(y_tr.unique()):
            skipped += 1
            continue

        try:
            y_fit = y_tr
            y_score = y_val
            if encode_labels:
                encoder = LabelEncoder()
                y_fit = pd.Series(encoder.fit_transform(y_tr), index=y_tr.index)
                y_score = pd.Series(encoder.transform(y_val), index=y_val.index)

            model = model_factory()
            model.fit(X_tr, y_fit)
            preds = model.predict(X_val)
            scores.append(float(f1_score(y_score, preds, average="macro", zero_division=0)))
        except Exception as e:
            logger.warning(f"CV fold 실패: {e}")
            skipped += 1

    mean = float(np.mean(scores)) if scores else 0.0
    std = float(np.std(scores)) if scores else 0.0
    return {
        "mean_f1": round(mean, 4),
        "std_f1": round(std, 4),
        "folds": [round(s, 4) for s in scores],
        "used_folds": len(scores),
        "skipped_folds": skipped,
    }


# ════════════════════════════════════════════════════════════
# LGBMOptimizer — 메인 클래스
# ════════════════════════════════════════════════════════════
class LGBMOptimizer:
    def __init__(self, kis_api):
        self.kis = kis_api
        self._running  = False
        self._result: Optional[dict] = None
        self._progress: dict = {"status": "idle", "trial": 0, "total": 0, "best_score": 0}
        self._model: Optional[lgb.LGBMClassifier] = None
        self._feature_cols: List[str] = []
        self._best_params: dict = {}

    # ── 전체 파이프라인 실행 ────────────────────────────────
    async def run(self, symbol: str, n_trials: int = 50,
                  ohlcv_count: int = 500, broadcast: Callable = None):
        self._running = True
        self._progress = {"status": "collecting", "trial": 0, "total": n_trials, "best_score": 0, "symbol": symbol}
        await self._notify(broadcast, "collecting", f"{symbol} 데이터 수집 중 ({ohlcv_count}일)...")

        try:
            # 1. 데이터 수집
            ohlcv = await self.kis.get_ohlcv(symbol, "D", ohlcv_count)
            if len(ohlcv) < 100:
                raise ValueError(f"데이터 부족: {len(ohlcv)}일 (최소 100일 필요)")

            await self._notify(broadcast, "feature", f"피처 생성 중... ({len(ohlcv)}일 데이터)")

            # 2. 피처 생성 (blocking → executor)
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, build_features, ohlcv)
            if df is None:
                raise ValueError(f"피처 생성 실패 — ohlcv {len(ohlcv)}일, 최소 70일 필요")
            if len(df) < 30:
                raise ValueError(f"NaN 제거 후 데이터 부족: {len(df)}행 (최소 30행 필요)")
            meta = {"date","open","high","low","close","volume"}
            feat_count = len([c for c in df.columns if c not in meta])
            logger.info(f"피처 생성 완료: {len(df)}행 × {feat_count}개 피처")

            feat_cols = [c for c in df.columns if c not in {"date","open","high","low","close","volume"}]
            self._feature_cols = feat_cols

            await self._notify(broadcast, "optimizing",
                               f"Optuna 최적화 시작 ({n_trials} trials)...")

            # 3. Optuna 탐색 (blocking → executor)
            study, best_horizon, best_buy_thr, best_sell_thr = \
                await loop.run_in_executor(None,
                    self._run_optuna, df, feat_cols, n_trials, broadcast, loop)

            best = study.best_trial
            self._best_params = {k: v for k, v in best.params.items()
                                 if k not in ("horizon","buy_threshold","sell_threshold")}

            # 4. 최적 파라미터로 전체 데이터 재학습
            await self._notify(broadcast, "training", "최적 파라미터로 최종 모델 학습 중...")
            labels = make_labels(df, horizon=best_horizon,
                                 buy_threshold=best_buy_thr,
                                 sell_threshold=best_sell_thr)
            valid = labels.notna()
            X = df.loc[valid, feat_cols]
            y = labels[valid]

            self._best_params.update({
                "objective":"multiclass","num_class":3,
                "verbosity":-1,"metric":"multi_logloss",
            })
            self._model = lgb.LGBMClassifier(**self._best_params)
            await loop.run_in_executor(None, lambda: self._model.fit(X, y))

            # 5. 백테스트
            preds_all = self._model.predict(X)
            bt = backtest(df.loc[valid].reset_index(drop=True), preds_all)

            await self._notify(broadcast, "cv", "LightGBM / XGBoost 교차검증 비교 중...")
            cv_compare = await loop.run_in_executor(
                None,
                self._compare_models_cv,
                df,
                feat_cols,
                best_horizon,
                best_buy_thr,
                best_sell_thr,
                self._best_params.copy(),
            )

            # 피처 중요도 Top15
            importance = sorted(
                zip(feat_cols, self._model.feature_importances_),
                key=lambda x: -x[1]
            )[:15]

            # 모델 저장
            model_path = MODEL_DIR / f"{symbol}_lgbm.txt"
            self._model.booster_.save_model(str(model_path))
            meta_path = MODEL_DIR / f"{symbol}_meta.json"
            meta = {
                "symbol": symbol,
                "trained_at": datetime.now().isoformat(),
                "best_params": best.params,
                "feature_cols": feat_cols,
                "horizon": best_horizon,
                "buy_threshold": best_buy_thr,
                "sell_threshold": best_sell_thr,
                "backtest": bt,
                "importance": [{"feature": f, "score": int(s)} for f, s in importance],
                "cv_compare": cv_compare,
            }
            meta_path.write_text(json.dumps(_json_safe(meta), ensure_ascii=False, indent=2))

            self._result = _json_safe({
                "symbol":        symbol,
                "status":        "done",
                "trained_at":    datetime.now().isoformat(),
                "data_days":     len(ohlcv),
                "feature_count": len(feat_cols),
                "n_trials":      n_trials,
                "best_score":    round(best.value, 4),
                "best_params": {
                    "horizon":         best_horizon,
                    "buy_threshold":   round(best_buy_thr, 4),
                    "sell_threshold":  round(best_sell_thr, 4),
                    "n_estimators":    best.params.get("n_estimators"),
                    "learning_rate":   round(best.params.get("learning_rate", 0), 4),
                    "num_leaves":      best.params.get("num_leaves"),
                    "max_depth":       best.params.get("max_depth"),
                },
                "backtest":      bt,
                "cv_compare":    cv_compare,
                "importance":    [{"feature": f, "score": int(s)} for f, s in importance],
            })

            await self._notify(broadcast, "done",
                               f"완료! 수익률 {bt['total_return']:+.1f}% | "
                               f"MDD {bt['mdd']:.1f}% | F1 {best.value:.3f}")
            logger.info(f"LGBM 최적화 완료: {symbol} → {self._result['best_params']}")

        except Exception as e:
            logger.error(f"LGBM 최적화 오류: {e}", exc_info=True)
            self._result = {"status": "error", "message": str(e)}
            await self._notify(broadcast, "error", f"오류: {e}")
        finally:
            self._running = False

    def _run_optuna(self, df, feat_cols, n_trials, broadcast, loop):
        """동기 컨텍스트에서 Optuna 실행"""
        tscv = TimeSeriesSplit(n_splits=5)

        # 가장 많이 사용될 horizon/threshold 기본값으로 레이블 생성
        # → objective 내부에서 trial마다 재생성
        X_full = df[feat_cols]
        y_dummy = make_labels(df, horizon=5, buy_threshold=0.02, sell_threshold=-0.01)
        valid = y_dummy.notna()
        X = X_full[valid]
        y = y_dummy[valid]

        best_horizon    = 5
        best_buy_thr   = 0.02
        best_sell_thr  = -0.01
        best_score     = -1.0

        def objective(trial):
            nonlocal best_horizon, best_buy_thr, best_sell_thr, best_score

            horizon   = trial.suggest_int("horizon", 3, 15)
            buy_thr   = trial.suggest_float("buy_threshold", 0.01, 0.05)
            sell_thr  = trial.suggest_float("sell_threshold", -0.05, -0.005)

            labels = make_labels(df, horizon=horizon,
                                 buy_threshold=buy_thr, sell_threshold=sell_thr)
            valid_  = labels.notna()
            X_      = df.loc[valid_, feat_cols]
            y_      = labels[valid_]

            params = {
                "objective":      "multiclass",
                "num_class":      3,
                "metric":         "multi_logloss",
                "verbosity":      -1,
                "boosting_type":  "gbdt",
                "n_estimators":   trial.suggest_int("n_estimators", 100, 600),
                "learning_rate":  trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                "num_leaves":     trial.suggest_int("num_leaves", 16, 96),
                "max_depth":      trial.suggest_int("max_depth", 3, 9),
                "min_child_samples": trial.suggest_int("min_child_samples", 10, 60),
                "subsample":      trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "reg_alpha":      trial.suggest_float("reg_alpha", 1e-6, 1.0, log=True),
                "reg_lambda":     trial.suggest_float("reg_lambda", 1e-6, 1.0, log=True),
            }
            scores = []
            for tr_idx, val_idx in tscv.split(X_):
                X_tr, X_val = X_.iloc[tr_idx], X_.iloc[val_idx]
                y_tr, y_val = y_.iloc[tr_idx], y_.iloc[val_idx]

                # train에 2종류 미만이거나 val에 train에 없는 레이블 있으면 스킵
                if len(y_tr.unique()) < 2:
                    continue
                unseen = set(y_val.unique()) - set(y_tr.unique())
                if unseen:
                    continue

                try:
                    m = lgb.LGBMClassifier(**params)
                    m.fit(X_tr, y_tr,
                          eval_set=[(X_val, y_val)],
                          callbacks=[lgb.early_stopping(40, verbose=False),
                                     lgb.log_evaluation(-1)])
                    score = f1_score(y_val, m.predict(X_val),
                                     average="macro", zero_division=0)
                    scores.append(score)
                except Exception:
                    continue

            mean_score = float(np.mean(scores)) if scores else 0.0

            if mean_score > best_score:
                best_score   = mean_score
                best_horizon  = horizon
                best_buy_thr  = buy_thr
                best_sell_thr = sell_thr

            # 진행률 업데이트 (thread-safe)
            self._progress["trial"]      = trial.number + 1
            self._progress["best_score"] = round(best_score, 4)
            asyncio.run_coroutine_threadsafe(
                self._notify(broadcast, "trial",
                             f"Trial {trial.number+1}/{n_trials} | "
                             f"Best F1: {best_score:.4f} | "
                             f"현재: {mean_score:.4f}"),
                loop
            )
            return mean_score

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        return study, best_horizon, best_buy_thr, best_sell_thr

    def _compare_models_cv(self, df, feat_cols, horizon, buy_thr, sell_thr, lgbm_params):
        labels = make_labels(df, horizon=horizon,
                             buy_threshold=buy_thr, sell_threshold=sell_thr)
        valid = labels.notna()
        X = df.loc[valid, feat_cols]
        y = labels[valid]
        tscv = TimeSeriesSplit(n_splits=5)

        lgbm_cv_params = {
            **lgbm_params,
            "objective": "multiclass",
            "num_class": 3,
            "metric": "multi_logloss",
            "verbosity": -1,
        }
        lgbm_result = _cv_macro_f1(
            lambda: lgb.LGBMClassifier(**lgbm_cv_params),
            X, y, tscv
        )

        result = {
            "metric": "macro_f1",
            "split": "TimeSeriesSplit(n_splits=5)",
            "label_params": {
                "horizon": horizon,
                "buy_threshold": round(buy_thr, 4),
                "sell_threshold": round(sell_thr, 4),
            },
            "models": {
                "lightgbm": lgbm_result,
            },
            "winner": "lightgbm",
        }

        if xgb is None:
            result["models"]["xgboost"] = {
                "status": "missing_dependency",
                "message": "xgboost 패키지가 설치되어 있지 않습니다",
            }
            return result

        xgb_params = {
            "objective": "multi:softprob",
            "num_class": 3,
            "eval_metric": "mlogloss",
            "tree_method": "hist",
            "n_estimators": min(int(lgbm_params.get("n_estimators", 300)), 600),
            "learning_rate": float(lgbm_params.get("learning_rate", 0.05)),
            "max_depth": int(lgbm_params.get("max_depth", 6)),
            "subsample": float(lgbm_params.get("subsample", 0.8)),
            "colsample_bytree": float(lgbm_params.get("colsample_bytree", 0.8)),
            "reg_alpha": float(lgbm_params.get("reg_alpha", 0.0)),
            "reg_lambda": float(lgbm_params.get("reg_lambda", 1.0)),
            "random_state": 42,
            "verbosity": 0,
        }
        xgb_result = _cv_macro_f1(
            lambda: xgb.XGBClassifier(**xgb_params),
            X, y, tscv, encode_labels=True
        )
        result["models"]["xgboost"] = xgb_result

        if xgb_result["mean_f1"] > lgbm_result["mean_f1"]:
            result["winner"] = "xgboost"
        return result

    async def _notify(self, broadcast, status: str, message: str):
        self._progress["status"] = status
        self._progress["message"] = message
        if broadcast:
            await broadcast({
                "type":    "lgbm",
                "status":  status,
                "message": message,
                "progress": self._progress.copy(),
            })

    def predict_latest(self, ohlcv: list) -> Optional[dict]:
        """학습된 모델로 최신 데이터 예측"""
        if not self._feature_cols:
            return None
        booster = self._get_booster()
        if booster is None:
            return None
        df = build_features(ohlcv)
        if df is None or len(df) == 0:
            return None
        X = df[self._feature_cols].iloc[[-1]]
        # Booster.predict → shape (1, num_class)
        probs = booster.predict(X)
        prob = probs[0]
        pred = int(np.argmax(prob))
        label_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        return {
            "signal":    label_map[pred],
            "prob_hold": round(float(prob[0]), 4),
            "prob_buy":  round(float(prob[1]), 4),
            "prob_sell": round(float(prob[2]), 4),
        }

    def _get_booster(self) -> Optional[lgb.Booster]:
        """fitted 여부 무관하게 Booster 반환"""
        if self._model is None:
            return None
        # 직접 학습한 경우
        try:
            return self._model.booster_
        except Exception:
            pass
        # load_model로 불러온 경우 (_Booster에 저장)
        booster = getattr(self._model, "_Booster", None)
        if booster is not None:
            return booster
        return None

    def load_model(self, symbol: str) -> bool:
        """저장된 모델 로드"""
        model_path = MODEL_DIR / f"{symbol}_lgbm.txt"
        meta_path  = MODEL_DIR / f"{symbol}_meta.json"
        if not model_path.exists() or not meta_path.exists():
            return False
        try:
            booster = lgb.Booster(model_file=str(model_path))
            self._model = lgb.LGBMClassifier()
            self._model._Booster = booster   # sklearn wrapper에 직접 주입
            meta = json.loads(meta_path.read_text())
            self._feature_cols = meta["feature_cols"]
            self._best_params  = meta["best_params"]
            self._result = {**meta, "status": "loaded"}
            logger.info(f"모델 로드 완료: {symbol} (학습일: {meta['trained_at'][:10]})")
            return True
        except Exception as e:
            logger.warning(f"모델 로드 실패: {e}")
            return False

    def stop(self):
        self._running = False

    @property
    def result(self): return self._result
    @property
    def progress(self): return self._progress
    @property
    def is_running(self): return self._running
