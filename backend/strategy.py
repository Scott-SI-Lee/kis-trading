"""
자동매매 전략 모음
- GoldenCrossStrategy : 이동평균 골든/데드크로스
- RSIStrategy         : RSI 과매수/과매도
- BollingerStrategy   : 볼린저 밴드 돌파
- MACDStrategy        : MACD 시그널 크로스
- AutoTrader          : 전략 실행 엔진 (공통)
"""

import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── 공통 시그널 ──────────────────────────────────────────────
@dataclass
class Signal:
    type: str        # BUY / SELL / HOLD
    symbol: str
    price: int
    indicators: dict = field(default_factory=dict)
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── 공통 헬퍼 ────────────────────────────────────────────────
def _sma(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def _ema(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema

def _stddev(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    subset = prices[-period:]
    mean = sum(subset) / period
    return (sum((x - mean) ** 2 for x in subset) / period) ** 0.5


# ════════════════════════════════════════════════════════════
# 1. 골든크로스 / 데드크로스
# ════════════════════════════════════════════════════════════
class GoldenCrossStrategy:
    """
    단기 MA > 장기 MA 상향돌파 → 매수 (골든크로스)
    단기 MA < 장기 MA 하향돌파 → 매도 (데드크로스)
    """
    name = "골든크로스"

    def __init__(self, symbol: str, qty: int,
                 short_period: int = 5, long_period: int = 20):
        self.symbol = symbol
        self.qty = qty
        self.short_period = short_period
        self.long_period = long_period
        self._prev_short: Optional[float] = None
        self._prev_long:  Optional[float] = None

    def required_bars(self) -> int:
        return self.long_period + 5

    def analyze(self, ohlcv: list) -> Signal:
        closes = [r["close"] for r in ohlcv]
        price  = closes[-1]
        short_ma = _sma(closes, self.short_period)
        long_ma  = _sma(closes, self.long_period)

        sig = "HOLD"
        reason = "대기중"

        if short_ma and long_ma and self._prev_short and self._prev_long:
            if self._prev_short <= self._prev_long and short_ma > long_ma:
                sig, reason = "BUY", f"골든크로스 (MA{self.short_period} > MA{self.long_period})"
            elif self._prev_short >= self._prev_long and short_ma < long_ma:
                sig, reason = "SELL", f"데드크로스 (MA{self.short_period} < MA{self.long_period})"

        if short_ma: self._prev_short = short_ma
        if long_ma:  self._prev_long  = long_ma

        return Signal(
            type=sig, symbol=self.symbol, price=price, reason=reason,
            indicators={
                f"MA{self.short_period}": round(short_ma, 0) if short_ma else 0,
                f"MA{self.long_period}":  round(long_ma,  0) if long_ma  else 0,
            }
        )


# ════════════════════════════════════════════════════════════
# 2. RSI 과매수 / 과매도
# ════════════════════════════════════════════════════════════
class RSIStrategy:
    """
    RSI <= oversold   → 매수 (과매도 구간, 반등 기대)
    RSI >= overbought → 매도 (과매수 구간, 하락 기대)
    기본값: 기간 14, 과매도 30, 과매수 70
    """
    name = "RSI"

    def __init__(self, symbol: str, qty: int,
                 period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.symbol = symbol
        self.qty = qty
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def required_bars(self) -> int:
        return self.period + 10

    def _calc_rsi(self, closes: list) -> Optional[float]:
        if len(closes) < self.period + 1:
            return None
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        recent = deltas[-self.period:]
        gains  = [d if d > 0 else 0 for d in recent]
        losses = [-d if d < 0 else 0 for d in recent]
        avg_gain = sum(gains)  / self.period
        avg_loss = sum(losses) / self.period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def analyze(self, ohlcv: list) -> Signal:
        closes = [r["close"] for r in ohlcv]
        price  = closes[-1]
        rsi    = self._calc_rsi(closes)

        sig    = "HOLD"
        reason = f"RSI {rsi} — 대기중" if rsi else "RSI 계산 중"

        if rsi is not None:
            if rsi <= self.oversold:
                sig    = "BUY"
                reason = f"RSI {rsi} ≤ {self.oversold} (과매도 → 매수)"
            elif rsi >= self.overbought:
                sig    = "SELL"
                reason = f"RSI {rsi} ≥ {self.overbought} (과매수 → 매도)"

        return Signal(
            type=sig, symbol=self.symbol, price=price, reason=reason,
            indicators={
                "RSI": rsi or 0,
                "과매도선": self.oversold,
                "과매수선": self.overbought,
            }
        )


# ════════════════════════════════════════════════════════════
# 3. 볼린저 밴드
# ════════════════════════════════════════════════════════════
class BollingerStrategy:
    """
    종가 <= 하단밴드 → 매수 (과매도, 반등 기대)
    종가 >= 상단밴드 → 매도 (과열, 하락 기대)
    기본값: 20일 이동평균, 2σ
    """
    name = "볼린저밴드"

    def __init__(self, symbol: str, qty: int,
                 period: int = 20, k: float = 2.0):
        self.symbol = symbol
        self.qty = qty
        self.period = period
        self.k = k

    def required_bars(self) -> int:
        return self.period + 5

    def analyze(self, ohlcv: list) -> Signal:
        closes = [r["close"] for r in ohlcv]
        price  = closes[-1]
        ma     = _sma(closes, self.period)
        std    = _stddev(closes, self.period)

        if ma is None or std is None:
            return Signal(type="HOLD", symbol=self.symbol, price=price,
                          reason="밴드 계산 중", indicators={})

        upper = round(ma + self.k * std)
        lower = round(ma - self.k * std)
        mid   = round(ma)

        sig    = "HOLD"
        reason = f"밴드 내부 — 상단:{upper:,} / 하단:{lower:,}"

        if price <= lower:
            sig    = "BUY"
            reason = f"하단밴드 이탈 {price:,} ≤ {lower:,} → 매수"
        elif price >= upper:
            sig    = "SELL"
            reason = f"상단밴드 돌파 {price:,} ≥ {upper:,} → 매도"

        return Signal(
            type=sig, symbol=self.symbol, price=price, reason=reason,
            indicators={"상단밴드": upper, "중심선": mid, "하단밴드": lower}
        )


# ════════════════════════════════════════════════════════════
# 4. MACD
# ════════════════════════════════════════════════════════════
class MACDStrategy:
    """
    MACD선이 시그널선을 상향돌파 → 매수
    MACD선이 시그널선을 하향돌파 → 매도
    기본값: 단기 EMA 12, 장기 EMA 26, 시그널 9
    """
    name = "MACD"

    def __init__(self, symbol: str, qty: int,
                 fast: int = 12, slow: int = 26, signal_period: int = 9):
        self.symbol = symbol
        self.qty = qty
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period
        self._prev_macd:   Optional[float] = None
        self._prev_signal: Optional[float] = None

    def required_bars(self) -> int:
        return self.slow + self.signal_period + 5

    def analyze(self, ohlcv: list) -> Signal:
        closes = [r["close"] for r in ohlcv]
        price  = closes[-1]

        ema_fast = _ema(closes, self.fast)
        ema_slow = _ema(closes, self.slow)

        if ema_fast is None or ema_slow is None:
            return Signal(type="HOLD", symbol=self.symbol, price=price,
                          reason="MACD 계산 중", indicators={})

        macd_val = ema_fast - ema_slow

        # 시그널선: MACD의 EMA (간이 계산)
        k = 2 / (self.signal_period + 1)
        if self._prev_signal is None:
            signal_val = macd_val
        else:
            signal_val = macd_val * k + self._prev_signal * (1 - k)

        histogram = macd_val - signal_val
        sig    = "HOLD"
        reason = f"MACD {macd_val:.1f} / Signal {signal_val:.1f}"

        if self._prev_macd is not None and self._prev_signal is not None:
            if self._prev_macd <= self._prev_signal and macd_val > signal_val:
                sig    = "BUY"
                reason = f"MACD 상향돌파 → 매수 (MACD:{macd_val:.1f} > Signal:{signal_val:.1f})"
            elif self._prev_macd >= self._prev_signal and macd_val < signal_val:
                sig    = "SELL"
                reason = f"MACD 하향돌파 → 매도 (MACD:{macd_val:.1f} < Signal:{signal_val:.1f})"

        self._prev_macd   = macd_val
        self._prev_signal = signal_val

        return Signal(
            type=sig, symbol=self.symbol, price=price, reason=reason,
            indicators={
                "MACD":      round(macd_val,   2),
                "Signal":    round(signal_val, 2),
                "Histogram": round(histogram,  2),
            }
        )


# ════════════════════════════════════════════════════════════
# 전략 레지스트리 (API에서 이름으로 선택)
# ════════════════════════════════════════════════════════════
STRATEGY_MAP = {
    "golden_cross": GoldenCrossStrategy,
    "rsi":          RSIStrategy,
    "bollinger":    BollingerStrategy,
    "macd":         MACDStrategy,
}


# ════════════════════════════════════════════════════════════
# AutoTrader — 모든 전략 공통 실행 엔진
# ════════════════════════════════════════════════════════════
class AutoTrader:
    def __init__(self, kis_api):
        self.kis = kis_api
        self._running  = False
        self._task: Optional[asyncio.Task] = None
        self._strategy = None
        self._log: list = []
        self._interval: int = 60
        self._position: int = 0

    async def start(self, strategy, interval: int, broadcast: Callable):
        if self._running:
            await self.stop()
        self._strategy = strategy
        self._interval = interval
        self._running  = True
        self._task = asyncio.create_task(self._run_loop(broadcast))
        logger.info(f"AutoTrader 시작: {strategy.name} / {strategy.symbol}")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AutoTrader 중지")

    async def _run_loop(self, broadcast: Callable):
        while self._running:
            try:
                await self._check_and_trade(broadcast)
            except Exception as e:
                logger.error(f"전략 실행 오류: {e}")
                self._add_log("ERROR", str(e))
            await asyncio.sleep(self._interval)

    async def _check_and_trade(self, broadcast: Callable):
        strategy = self._strategy
        ohlcv    = await self.kis.get_ohlcv(strategy.symbol, "D", strategy.required_bars())
        signal: Signal = strategy.analyze(ohlcv)

        log_entry = {
            "time":       signal.timestamp,
            "strategy":   strategy.name,
            "symbol":     signal.symbol,
            "signal":     signal.type,
            "price":      signal.price,
            "reason":     signal.reason,
            "indicators": signal.indicators,
            "action":     None,
        }

        if signal.type == "BUY" and self._position == 0:
            result = await self.kis.place_order(strategy.symbol, "BUY", strategy.qty)
            self._position = strategy.qty
            log_entry["action"] = f"매수 {strategy.qty}주 @ {signal.price:,}원"
            logger.info(f"[{strategy.name}] 매수: {result}")
            await broadcast({"type": "trade", "signal": "BUY", "data": log_entry})

        elif signal.type == "SELL" and self._position > 0:
            result = await self.kis.place_order(strategy.symbol, "SELL", self._position)
            log_entry["action"] = f"매도 {self._position}주 @ {signal.price:,}원"
            self._position = 0
            logger.info(f"[{strategy.name}] 매도: {result}")
            await broadcast({"type": "trade", "signal": "SELL", "data": log_entry})

        else:
            await broadcast({"type": "signal", "data": log_entry})

        self._log.append(log_entry)
        if len(self._log) > 200:
            self._log = self._log[-200:]

    def _add_log(self, level: str, message: str):
        self._log.append({
            "time": datetime.now().isoformat(),
            "level": level,
            "message": message,
        })

    def status(self) -> dict:
        s = self._strategy
        return {
            "running":  self._running,
            "strategy": s.name if s else None,
            "symbol":   s.symbol if s else None,
            "qty":      s.qty if s else None,
            "position": self._position,
            "interval": self._interval,
        }

    def get_log(self) -> list:
        return list(reversed(self._log))
