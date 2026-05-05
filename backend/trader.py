import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional

from strategy import GoldenCrossStrategy

logger = logging.getLogger(__name__)


class AutoTrader:
    def __init__(self, kis_api):
        self.kis = kis_api
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._strategy: Optional[GoldenCrossStrategy] = None
        self._log: list = []
        self._interval: int = 60
        self._position: int = 0   # 보유 수량 추적

    async def start(self, strategy: GoldenCrossStrategy, interval: int, broadcast: Callable):
        if self._running:
            await self.stop()
        self._strategy = strategy
        self._interval = interval
        self._running = True
        self._task = asyncio.create_task(self._run_loop(broadcast))
        logger.info(f"AutoTrader 시작: {strategy.symbol}")

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
        ohlcv = await self.kis.get_ohlcv(strategy.symbol, "D", strategy.long_period + 5)
        signal = strategy.analyze(ohlcv)

        log_entry = {
            "time": signal.timestamp,
            "symbol": signal.symbol,
            "signal": signal.type,
            "price": signal.price,
            "short_ma": signal.short_ma,
            "long_ma": signal.long_ma,
            "action": None,
        }

        if signal.type == "GOLDEN_CROSS" and self._position == 0:
            result = await self.kis.place_order(strategy.symbol, "BUY", strategy.qty)
            self._position = strategy.qty
            log_entry["action"] = f"매수 {strategy.qty}주 @ {signal.price:,}원"
            logger.info(f"골든크로스 매수: {result}")
            await broadcast({"type": "trade", "signal": "BUY", "data": log_entry})

        elif signal.type == "DEAD_CROSS" and self._position > 0:
            result = await self.kis.place_order(strategy.symbol, "SELL", self._position)
            log_entry["action"] = f"매도 {self._position}주 @ {signal.price:,}원"
            self._position = 0
            logger.info(f"데드크로스 매도: {result}")
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
            "running": self._running,
            "symbol": s.symbol if s else None,
            "qty": s.qty if s else None,
            "short_period": s.short_period if s else None,
            "long_period": s.long_period if s else None,
            "position": self._position,
            "interval": self._interval,
        }

    def get_log(self) -> list:
        return list(reversed(self._log))
