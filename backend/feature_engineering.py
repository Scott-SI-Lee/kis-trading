"""
feature_engineering.py
일봉 OHLCV → 기술적 지표 + 거래량/가격 패턴 피처 생성
LightGBM 학습에 사용
"""

import numpy as np
import pandas as pd
from typing import Optional, List, Dict


# ── 기술적 지표 계산 ─────────────────────────────────────────

def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    for p in [5, 10, 20, 60]:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
        df[f"ma{p}_ratio"] = df["close"] / df[f"ma{p}"]   # 현재가 / MA 비율
    # MA 크로스 여부
    df["ma5_above_ma20"]  = (df["ma5"]  > df["ma20"]).astype(int)
    df["ma10_above_ma60"] = (df["ma10"] > df["ma60"]).astype(int)
    # MA 기울기 (변화율)
    df["ma5_slope"]  = df["ma5"].pct_change(3)
    df["ma20_slope"] = df["ma20"].pct_change(5)
    return df

def add_rsi(df: pd.DataFrame, periods: list = [7, 14, 21]) -> pd.DataFrame:
    for p in periods:
        delta  = df["close"].diff()
        gain   = delta.clip(lower=0).rolling(p).mean()
        loss   = (-delta.clip(upper=0)).rolling(p).mean()
        rs     = gain / loss.replace(0, np.nan)
        df[f"rsi{p}"] = 100 - (100 / (1 + rs))
    return df

def add_bollinger(df: pd.DataFrame, periods: list = [10, 20]) -> pd.DataFrame:
    for p in periods:
        ma  = df["close"].rolling(p).mean()
        std = df["close"].rolling(p).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        df[f"bb{p}_width"]    = (upper - lower) / ma        # 밴드 폭 (변동성)
        df[f"bb{p}_position"] = (df["close"] - lower) / (upper - lower)  # 밴드 내 위치 0~1
    return df

def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    df["macd"]      = macd
    df["macd_signal"] = signal
    df["macd_hist"] = macd - signal
    df["macd_above_signal"] = (macd > signal).astype(int)
    return df

def add_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    df["volume_ma5"]   = df["volume"].rolling(5).mean()
    df["volume_ma20"]  = df["volume"].rolling(20).mean()
    df["volume_ratio5"]  = df["volume"] / df["volume_ma5"]   # 단기 거래량 비율
    df["volume_ratio20"] = df["volume"] / df["volume_ma20"]  # 중기 거래량 비율
    # 거래량 증가율
    df["volume_change"] = df["volume"].pct_change()
    # 거래대금 (volume × close 근사)
    df["turnover"] = df["volume"] * df["close"]
    df["turnover_ma5"]  = df["turnover"].rolling(5).mean()
    df["turnover_ratio"] = df["turnover"] / df["turnover_ma5"]
    return df

def add_price_pattern(df: pd.DataFrame) -> pd.DataFrame:
    # 등락률
    df["return_1d"] = df["close"].pct_change(1)
    df["return_3d"] = df["close"].pct_change(3)
    df["return_5d"] = df["close"].pct_change(5)
    df["return_10d"] = df["close"].pct_change(10)
    # 고가/저가 대비 위치
    df["hl_range"]    = (df["high"] - df["low"]) / df["close"]  # 일중 변동폭
    df["close_in_hl"] = (df["close"] - df["low"]) / (df["high"] - df["low"] + 1e-9)
    # 갭
    df["gap_ratio"] = (df["open"] - df["close"].shift(1)) / df["close"].shift(1)
    # 연속 상승/하락일 수 (안전한 반복 방식)
    streak_up   = np.zeros(len(df), dtype=int)
    streak_down = np.zeros(len(df), dtype=int)
    for i in range(1, len(df)):
        r = df["return_1d"].iloc[i]
        if r > 0:
            streak_up[i]   = streak_up[i-1] + 1
            streak_down[i] = 0
        elif r < 0:
            streak_down[i] = streak_down[i-1] + 1
            streak_up[i]   = 0
    df["streak_up"]   = streak_up
    df["streak_down"] = streak_down
    # 변동성 (20일 수익률 표준편차)
    df["volatility_20"] = df["return_1d"].rolling(20).std()
    return df

def add_candlestick_patterns(df: pd.DataFrame) -> pd.DataFrame:
    body    = df["close"] - df["open"]
    hl      = df["high"] - df["low"]
    upper_w = df["high"] - df[["close","open"]].max(axis=1)
    lower_w = df[["close","open"]].min(axis=1) - df["low"]
    df["body_ratio"]   = body.abs() / (hl + 1e-9)     # 몸통 비율
    df["upper_shadow"]  = upper_w / (hl + 1e-9)       # 위 꼬리 비율
    df["lower_shadow"]  = lower_w / (hl + 1e-9)       # 아래 꼬리 비율
    df["is_bullish"]    = (body > 0).astype(int)       # 양봉 여부
    df["is_doji"]       = (df["body_ratio"] < 0.1).astype(int)  # 도지
    return df


# ── 메인 피처 생성 ───────────────────────────────────────────
FEATURE_COLS: list[str] = []   # build_features 후 채워짐

def build_features(ohlcv: List[Dict]) -> Optional[pd.DataFrame]:
    """
    ohlcv: [{"date","open","high","low","close","volume"}, ...]
    returns: 피처 DataFrame (NaN 행 제거)
    """
    if len(ohlcv) < 70:
        return None

    df = pd.DataFrame(ohlcv)
    df = df.sort_values("date").reset_index(drop=True)
    df[["open","high","low","close","volume"]] = \
        df[["open","high","low","close","volume"]].apply(pd.to_numeric, errors="coerce")

    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_bollinger(df)
    df = add_macd(df)
    df = add_volume_features(df)
    df = add_price_pattern(df)
    df = add_candlestick_patterns(df)

    # 피처 컬럼만 추출
    meta_cols = {"date","open","high","low","close","volume"}
    feat_cols = [c for c in df.columns if c not in meta_cols]

    # NaN 제거 (일부 컬럼만 NaN인 행은 0으로 채워 보존)
    # 핵심 피처만 dropna, 나머지는 0 fill
    core_cols = [c for c in feat_cols if any(k in c for k in
                 ["rsi14","ma20","macd","bb20","volume_ratio20","return_1d"])]
    df = df.dropna(subset=core_cols)
    df[feat_cols] = df[feat_cols].fillna(0)
    df = df.reset_index(drop=True)

    global FEATURE_COLS
    FEATURE_COLS = feat_cols

    return df


def make_labels(df: pd.DataFrame, horizon: int = 5,
                buy_threshold: float = 0.02,
                sell_threshold: float = -0.01) -> pd.Series:
    """
    horizon일 후 수익률로 레이블 생성
      1 (BUY)  : horizon일 후 수익률 >= buy_threshold
      2 (SELL) : horizon일 후 수익률 <= sell_threshold
      0 (HOLD) : 그 외
    """
    future_return = df["close"].shift(-horizon) / df["close"] - 1
    labels = pd.Series(0, index=df.index, name="label")
    labels[future_return >= buy_threshold]  = 1   # BUY
    labels[future_return <= sell_threshold] = 2   # SELL
    return labels
