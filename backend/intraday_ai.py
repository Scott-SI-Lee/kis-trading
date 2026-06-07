"""
intraday_ai.py

1분봉 기반 급등주 탐지/스코어링 엔진.
- Label: 현재가 대비 향후 15분 최고가가 +2% 이상 도달하면 1, 아니면 0
- 학습: TimeSeriesSplit / Walk Forward Validation, 랜덤 셔플 금지
- Optuna 목표: 백테스트 성과(Profit Factor, Sharpe, MDD, Win Rate) 기반
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
from sklearn.metrics import precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit

logger = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)

MODEL_DIR = Path(__file__).parent / "models" / "intraday"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


FEATURE_COLS = [
    "return_1m",
    "return_3m",
    "return_5m",
    "return_10m",
    "open_return",
    "vwap_deviation",
    "volume_growth",
    "turnover_growth",
    "volume_ratio_5m",
    "volume_ratio_15m",
    "rsi14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_position",
    "bb_width_change",
    "break_day_high",
    "break_15m_high",
    "break_30m_high",
    "kospi_return",
    "kosdaq_return",
    "market_turnover_growth",
    "total_bid_qty",
    "total_ask_qty",
    "bid_ask_ratio",
    "orderbook_imbalance",
    "orderbook_change_rate",
]


def _to_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float)


def _safe_div(numerator, denominator):
    return numerator / denominator.replace(0, np.nan)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def build_intraday_features(
    bars: list[dict],
    market_context: Optional[dict] = None,
    orderbook: Optional[dict] = None,
) -> Optional[pd.DataFrame]:
    """
    bars: 오래된 순서 -> 최신 순서의 1분봉
      필수: datetime/date/time, open, high, low, close, volume
      선택: turnover, kospi_return, kosdaq_return, market_turnover_growth,
            total_bid_qty, total_ask_qty, orderbook_change_rate
    """
    if len(bars) < 60:
        return None

    df = pd.DataFrame(bars).copy()
    if "datetime" not in df.columns:
        if "date" in df.columns and "time" in df.columns:
            df["datetime"] = df["date"].astype(str) + df["time"].astype(str)
        elif "date" in df.columns:
            df["datetime"] = df["date"].astype(str)
        else:
            df["datetime"] = np.arange(len(df))

    df = df.sort_values("datetime").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = _to_float(df[col])

    if "turnover" not in df.columns:
        df["turnover"] = df["close"] * df["volume"]
    else:
        df["turnover"] = _to_float(df["turnover"])

    df["return_1m"] = df["close"].pct_change(1)
    df["return_3m"] = df["close"].pct_change(3)
    df["return_5m"] = df["close"].pct_change(5)
    df["return_10m"] = df["close"].pct_change(10)
    day_open = df["open"].iloc[0]
    df["open_return"] = df["close"] / day_open - 1 if day_open else 0

    cum_turnover = df["turnover"].cumsum()
    cum_volume = df["volume"].cumsum()
    df["vwap"] = cum_turnover / cum_volume.replace(0, np.nan)
    df["vwap_deviation"] = df["close"] / df["vwap"] - 1

    df["volume_growth"] = df["volume"].pct_change(1)
    df["turnover_growth"] = df["turnover"].pct_change(1)
    df["volume_ratio_5m"] = _safe_div(df["volume"], df["volume"].rolling(5).mean())
    df["volume_ratio_15m"] = _safe_div(df["volume"], df["volume"].rolling(15).mean())

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = (bb_upper - bb_lower) / bb_mid.replace(0, np.nan)
    df["bb_position"] = (df["close"] - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)
    df["bb_width_change"] = bb_width.pct_change(5)

    day_high_prev = df["high"].cummax().shift(1)
    high_15_prev = df["high"].rolling(15).max().shift(1)
    high_30_prev = df["high"].rolling(30).max().shift(1)
    df["break_day_high"] = (df["close"] > day_high_prev).astype(int)
    df["break_15m_high"] = (df["close"] > high_15_prev).astype(int)
    df["break_30m_high"] = (df["close"] > high_30_prev).astype(int)

    context = market_context or {}
    df["kospi_return"] = _to_float(df.get("kospi_return", pd.Series(context.get("kospi_return", 0), index=df.index)))
    df["kosdaq_return"] = _to_float(df.get("kosdaq_return", pd.Series(context.get("kosdaq_return", 0), index=df.index)))
    df["market_turnover_growth"] = _to_float(
        df.get("market_turnover_growth", pd.Series(context.get("market_turnover_growth", 0), index=df.index))
    )

    ob = orderbook or {}
    df["total_bid_qty"] = _to_float(df.get("total_bid_qty", pd.Series(ob.get("total_bid_qty", 0), index=df.index)))
    df["total_ask_qty"] = _to_float(df.get("total_ask_qty", pd.Series(ob.get("total_ask_qty", 0), index=df.index)))
    df["bid_ask_ratio"] = _safe_div(df["total_bid_qty"], df["total_ask_qty"])
    df["orderbook_imbalance"] = _safe_div(
        df["total_bid_qty"] - df["total_ask_qty"],
        df["total_bid_qty"] + df["total_ask_qty"],
    )
    df["orderbook_change_rate"] = _to_float(
        df.get("orderbook_change_rate", pd.Series(ob.get("orderbook_change_rate", 0), index=df.index))
    )

    df[FEATURE_COLS] = df[FEATURE_COLS].replace([np.inf, -np.inf], np.nan).fillna(0)
    return df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)


def make_intraday_labels(df: pd.DataFrame, horizon: int = 15, threshold: float = 0.02) -> pd.Series:
    future_high = df["high"].shift(-1).rolling(horizon, min_periods=horizon).max().shift(-(horizon - 1))
    labels = (future_high / df["close"] - 1 >= threshold).astype(float)
    labels.iloc[-horizon:] = np.nan
    return labels.rename("label")


@dataclass
class ExitStrategy:
    name: str
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    trailing_stop: Optional[float] = None
    time_exit_minutes: Optional[int] = None


EXIT_STRATEGIES = {
    "A": ExitStrategy("A", take_profit=0.03, stop_loss=-0.015),
    "B": ExitStrategy("B", trailing_stop=0.01, time_exit_minutes=15),
    "C": ExitStrategy("C", time_exit_minutes=15),
}


def backtest_intraday(
    df: pd.DataFrame,
    probabilities: np.ndarray,
    threshold: float = 0.75,
    strategy: ExitStrategy = EXIT_STRATEGIES["A"],
    commission: float = 0.00015,
) -> dict:
    entries = probabilities >= threshold
    equity = 1_000_000.0
    equity_curve = [equity]
    trades = []
    i = 0
    n = len(df)

    while i < n - 1:
        if not entries[i]:
            equity_curve.append(equity)
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_price = float(df["open"].iloc[entry_idx])
        if entry_price <= 0:
            i += 1
            continue

        max_hold = strategy.time_exit_minutes or 15
        exit_idx = min(entry_idx + max_hold, n - 1)
        peak = entry_price
        exit_reason = "time"
        exit_price = float(df["close"].iloc[exit_idx])

        for j in range(entry_idx, exit_idx + 1):
            high = float(df["high"].iloc[j])
            low = float(df["low"].iloc[j])
            peak = max(peak, high)

            if strategy.take_profit is not None and high >= entry_price * (1 + strategy.take_profit):
                exit_idx = j
                exit_price = entry_price * (1 + strategy.take_profit)
                exit_reason = "take_profit"
                break
            if strategy.stop_loss is not None and low <= entry_price * (1 + strategy.stop_loss):
                exit_idx = j
                exit_price = entry_price * (1 + strategy.stop_loss)
                exit_reason = "stop_loss"
                break
            if strategy.trailing_stop is not None and low <= peak * (1 - strategy.trailing_stop):
                exit_idx = j
                exit_price = peak * (1 - strategy.trailing_stop)
                exit_reason = "trailing_stop"
                break

        net_return = (exit_price / entry_price - 1) - commission * 2
        equity *= 1 + net_return
        hold_minutes = max(1, exit_idx - entry_idx + 1)
        trades.append({
            "entry_idx": int(entry_idx),
            "exit_idx": int(exit_idx),
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "return_pct": round(net_return * 100, 3),
            "hold_minutes": hold_minutes,
            "reason": exit_reason,
        })
        equity_curve.extend([equity] * hold_minutes)
        i = exit_idx + 1

    returns = pd.Series([t["return_pct"] / 100 for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    profit_factor = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else (float("inf") if len(wins) else 0.0)
    sharpe = float(returns.mean() / returns.std() * np.sqrt(252 * 24)) if returns.std() and returns.std() > 0 else 0.0
    curve = pd.Series(equity_curve)

    return {
        "strategy": strategy.name,
        "final_value": round(equity),
        "total_return": round((equity / 1_000_000 - 1) * 100, 2),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else 999.0,
        "sharpe": round(sharpe, 4),
        "mdd": round(_max_drawdown(curve) * 100, 2),
        "win_rate": round(float((returns > 0).mean() * 100), 2) if len(returns) else 0.0,
        "avg_hold_minutes": round(float(np.mean([t["hold_minutes"] for t in trades])), 2) if trades else 0.0,
        "num_trades": len(trades),
        "trades": trades[-30:],
    }


def _objective_score(bt: dict) -> float:
    return (
        min(float(bt["profit_factor"]), 10.0) * 1000
        + float(bt["sharpe"]) * 100
        + float(bt["mdd"]) * 10
        + float(bt["win_rate"])
    )


def _feature_importance(model: lgb.LGBMClassifier, feature_cols: list[str]) -> list[dict]:
    return [
        {"feature": f, "score": int(s)}
        for f, s in sorted(zip(feature_cols, model.feature_importances_), key=lambda x: -x[1])[:30]
    ]


def _shap_importance(model: lgb.LGBMClassifier, X: pd.DataFrame) -> list[dict]:
    try:
        import shap

        sample = X.tail(min(len(X), 1000))
        values = shap.TreeExplainer(model).shap_values(sample)
        if isinstance(values, list):
            values = values[1] if len(values) > 1 else values[0]
        scores = np.abs(values).mean(axis=0)
        return [
            {"feature": f, "score": float(round(s, 6))}
            for f, s in sorted(zip(X.columns, scores), key=lambda x: -x[1])[:30]
        ]
    except Exception as e:
        logger.warning(f"SHAP importance 계산 실패: {e}")
        return []


class IntradayAIEngine:
    def __init__(self):
        self._running = False
        self._progress = {"status": "idle", "trial": 0, "total": 0, "best_score": 0}
        self._result: Optional[dict] = None
        self._model: Optional[lgb.LGBMClassifier] = None
        self._feature_cols = FEATURE_COLS.copy()
        self._best_params: dict = {}

    @property
    def result(self):
        return self._result

    @property
    def progress(self):
        return self._progress

    @property
    def is_running(self):
        return self._running

    def stop(self):
        self._running = False

    def train(
        self,
        symbol: str,
        bars: list[dict],
        n_trials: int = 50,
        market_context: Optional[dict] = None,
        orderbook: Optional[dict] = None,
        notify: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        self._running = True
        self._progress = {"status": "feature", "trial": 0, "total": n_trials, "best_score": 0, "symbol": symbol}
        try:
            df = build_intraday_features(bars, market_context, orderbook)
            if df is None or len(df) < 120:
                raise ValueError("1분봉 데이터가 부족합니다. 최소 120개 이상이 필요합니다.")

            labels = make_intraday_labels(df, horizon=15, threshold=0.02)
            valid = labels.notna()
            X = df.loc[valid, self._feature_cols]
            y = labels.loc[valid].astype(int)
            if y.nunique() < 2:
                raise ValueError("레이블이 한쪽 클래스만 존재합니다. 더 긴 장중 데이터가 필요합니다.")

            tscv = TimeSeriesSplit(n_splits=min(5, max(2, len(X) // 80)))
            best_score = -1e9

            def objective(trial: optuna.Trial) -> float:
                nonlocal best_score
                params = {
                    "objective": "binary",
                    "metric": "binary_logloss",
                    "verbosity": -1,
                    "boosting_type": "gbdt",
                    "n_estimators": trial.suggest_int("n_estimators", 100, 700),
                    "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.2, log=True),
                    "num_leaves": trial.suggest_int("num_leaves", 16, 128),
                    "max_depth": trial.suggest_int("max_depth", 3, 12),
                    "min_child_samples": trial.suggest_int("min_child_samples", 10, 100),
                    "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
                    "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
                    "bagging_freq": 1,
                    "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
                    "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
                    "random_state": 42,
                }
                threshold = trial.suggest_float("entry_threshold", 0.6, 0.9)
                scores = []
                for tr_idx, val_idx in tscv.split(X):
                    if not self._running:
                        break
                    X_tr, X_val = X.iloc[tr_idx], X.iloc[val_idx]
                    y_tr = y.iloc[tr_idx]
                    if y_tr.nunique() < 2:
                        continue
                    model = lgb.LGBMClassifier(**params)
                    model.fit(X_tr, y_tr)
                    probs = model.predict_proba(X_val)[:, 1]
                    bt = backtest_intraday(df.loc[X_val.index].reset_index(drop=True), probs, threshold, EXIT_STRATEGIES["A"])
                    scores.append(_objective_score(bt))

                score = float(np.mean(scores)) if scores else -1e9
                best_score = max(best_score, score)
                self._progress.update({"status": "optimizing", "trial": trial.number + 1, "best_score": round(best_score, 4)})
                if notify:
                    notify(self._progress.copy())
                return score

            study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
            params = study.best_params.copy()
            entry_threshold = float(params.pop("entry_threshold", 0.75))
            params.update({"objective": "binary", "metric": "binary_logloss", "verbosity": -1, "boosting_type": "gbdt", "random_state": 42})
            self._best_params = params

            split = int(len(X) * 0.8)
            X_train, X_test = X.iloc[:split], X.iloc[split:]
            y_train, y_test = y.iloc[:split], y.iloc[split:]
            self._model = lgb.LGBMClassifier(**params)
            self._model.fit(X_train, y_train)
            test_probs = self._model.predict_proba(X_test)[:, 1]
            test_preds = (test_probs >= entry_threshold).astype(int)

            backtests = {
                key: backtest_intraday(df.loc[X_test.index].reset_index(drop=True), test_probs, entry_threshold, strategy)
                for key, strategy in EXIT_STRATEGIES.items()
            }
            best_strategy_key = max(backtests, key=lambda k: _objective_score(backtests[k]))

            final_model = lgb.LGBMClassifier(**params)
            final_model.fit(X, y)
            self._model = final_model

            importance = _feature_importance(final_model, self._feature_cols)
            shap_importance = _shap_importance(final_model, X)

            model_path = MODEL_DIR / f"{symbol}_intraday_lgbm.txt"
            meta_path = MODEL_DIR / f"{symbol}_intraday_meta.json"
            final_model.booster_.save_model(str(model_path))
            meta = {
                "symbol": symbol,
                "trained_at": datetime.now().isoformat(),
                "feature_cols": self._feature_cols,
                "best_params": study.best_params,
                "entry_threshold": entry_threshold,
                "label": {"horizon_minutes": 15, "threshold": 0.02, "target": "future_15m_high"},
                "precision": precision_score(y_test, test_preds, zero_division=0),
                "recall": recall_score(y_test, test_preds, zero_division=0),
                "backtests": backtests,
                "best_exit_strategy": best_strategy_key,
                "importance": importance,
                "shap_importance": shap_importance,
            }
            meta_path.write_text(json.dumps(_json_safe(meta), ensure_ascii=False, indent=2))
            self._result = _json_safe({"status": "done", **meta})
            return self._result
        except Exception as e:
            logger.error(f"Intraday AI 학습 실패: {e}", exc_info=True)
            self._result = {"status": "error", "message": str(e)}
            return self._result
        finally:
            self._running = False
            self._progress["status"] = self._result.get("status", "done") if self._result else "done"

    def load_model(self, symbol: str) -> bool:
        model_path = MODEL_DIR / f"{symbol}_intraday_lgbm.txt"
        meta_path = MODEL_DIR / f"{symbol}_intraday_meta.json"
        if not model_path.exists() or not meta_path.exists():
            return False
        booster = lgb.Booster(model_file=str(model_path))
        self._model = booster
        self._result = json.loads(meta_path.read_text())
        self._feature_cols = self._result.get("feature_cols") or FEATURE_COLS.copy()
        return True

    def predict_latest(
        self,
        bars: list[dict],
        market_context: Optional[dict] = None,
        orderbook: Optional[dict] = None,
    ) -> Optional[dict]:
        if self._model is None:
            return None
        df = build_intraday_features(bars, market_context, orderbook)
        if df is None or df.empty:
            return None
        X = df[self._feature_cols].iloc[[-1]]
        if isinstance(self._model, lgb.Booster):
            prob = float(self._model.predict(X)[0])
        else:
            prob = float(self._model.predict_proba(X)[:, 1][0])

        last = df.iloc[-1]
        turnover_score = min(max(float(last.get("turnover_growth", 0)) + 1, 0), 3)
        turnover_score = 0.5 + turnover_score / 3
        breakout_score = 1.0 + 0.15 * (
            int(last.get("break_day_high", 0)) + int(last.get("break_15m_high", 0)) + int(last.get("break_30m_high", 0))
        )
        final_score = float(np.clip(prob * turnover_score * breakout_score, 0, 1))
        threshold = float((self._result or {}).get("entry_threshold", 0.75))
        is_buy_candidate = bool(
            prob >= threshold
            and last.get("turnover_growth", 0) > 0
            and int(last.get("break_15m_high", 0)) == 1
        )
        return {
            "probability": round(prob, 4),
            "final_score": round(final_score, 4),
            "entry_threshold": round(threshold, 4),
            "is_buy_candidate": is_buy_candidate,
            "price": round(float(last["close"]), 2),
            "features": {k: round(float(last[k]), 6) for k in self._feature_cols if k in last},
        }


def rank_candidates(candidates: list[dict], limit: int = 20) -> list[dict]:
    ranked = sorted(candidates, key=lambda x: x.get("score", {}).get("final_score", 0), reverse=True)
    for idx, item in enumerate(ranked, start=1):
        item["rank"] = idx
    return ranked[:limit]


def analyze_pre_surge_patterns(df: pd.DataFrame, labels: pd.Series) -> dict:
    valid = labels.notna()
    work = df.loc[valid].copy()
    work["label"] = labels.loc[valid].astype(int)
    positives = work[work["label"] == 1]
    negatives = work[work["label"] == 0]

    checks = {
        "거래대금 급증": "turnover_growth",
        "신고가 돌파": "break_15m_high",
        "체결강도 증가": "execution_strength",
        "호가 불균형": "orderbook_imbalance",
        "VI 해제 이후 움직임": "vi_released",
    }
    result = {}
    for name, col in checks.items():
        if col not in work.columns:
            result[name] = {"available": False, "useful": False}
            continue
        pos_mean = float(positives[col].mean()) if len(positives) else 0.0
        neg_mean = float(negatives[col].mean()) if len(negatives) else 0.0
        lift = pos_mean - neg_mean
        result[name] = {
            "available": True,
            "positive_mean": round(pos_mean, 6),
            "negative_mean": round(neg_mean, 6),
            "lift": round(lift, 6),
            "useful": bool(lift > 0),
            "auto_feature": bool(lift > 0 and col in FEATURE_COLS),
        }
    return result


def _json_safe(value):
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
    return value
