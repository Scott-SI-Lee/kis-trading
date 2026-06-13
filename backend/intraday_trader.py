"""
IntradayAutoTrader - 장중 AI 신호 기반 단타 자동매매
1분봉 기반 실시간 모니터링 + 익절/손절 관리
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from intraday_ai import IntradayAIEngine

logger = logging.getLogger(__name__)


class Position:
    """진입한 포지션 추적"""
    def __init__(self, symbol: str, entry_price: float, qty: int, entry_time: datetime):
        self.symbol = symbol
        self.entry_price = entry_price
        self.qty = qty
        self.entry_time = entry_time
        self.peak_price = entry_price
        self.current_price = entry_price
        self.pnl = 0.0
        self.pnl_pct = 0.0

    def update_price(self, price: float):
        """현재가 업데이트"""
        self.current_price = price
        self.peak_price = max(self.peak_price, price)
        self.pnl = (price - self.entry_price) * self.qty
        self.pnl_pct = (price / self.entry_price - 1) * 100

    def elapsed_minutes(self) -> int:
        """진입 후 경과 시간 (분)"""
        return int((datetime.now() - self.entry_time).total_seconds() / 60)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_price": round(self.entry_price, 2),
            "current_price": round(self.current_price, 2),
            "qty": self.qty,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "elapsed_minutes": self.elapsed_minutes(),
            "peak_price": round(self.peak_price, 2),
        }


class IntradayAutoTrader:
    def __init__(self, kis_api):
        self.kis = kis_api
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._position: Optional[Position] = None
        self._log: list = []
        self._total_pnl = 0.0
        self._trades_closed = 0

        # 설정 (카스터마이징 가능)
        self.entry_threshold = 0.75  # AI 진입 확률
        self.take_profit = 0.03  # 익절 3%
        self.stop_loss = -0.015  # 손절 -1.5%
        self.time_exit_minutes = 15  # 15분 경과 시 자동 청산
        self.max_position_qty = 10  # 최대 진입 수량
        self.max_daily_loss = -100000  # 일일 최대 손실 -100k

    async def start(self, broadcast: Callable):
        """자동매매 시작"""
        if self._running:
            await self.stop()

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_rank_loop(broadcast))
        self._task = asyncio.create_task(self._manage_position_loop(broadcast))
        logger.info("🚀 IntradayAutoTrader 시작")

    async def stop(self):
        """자동매매 중지"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        # 진행 중인 포지션 강제 청산
        if self._position:
            await self._exit_position("forced_stop", broadcast=None)
        logger.info("⏹️  IntradayAutoTrader 중지")

    async def _monitor_rank_loop(self, broadcast: Callable):
        """장중 AI 랭킹 모니터링 (30초 마다)"""
        while self._running:
            try:
                # 이미 진입했으면 스킵
                if self._position:
                    await asyncio.sleep(30)
                    continue

                # 장중 AI 랭킹 실행
                await self.kis.get_access_token()  # 토큰 갱신

                # AI 예측을 통해 상위 후보 찾기
                from main import intraday_ai as global_intraday_ai
                from intraday_ai import IntradayAIEngine

                # intraday_ai_rank 로직 모방 (간단화)
                candidates = await self._scan_ai_candidates()

                if candidates:
                    best = candidates[0]
                    symbol = best["symbol"]
                    score = best.get("score", {})
                    prob = score.get("probability", 0)

                    await broadcast({
                        "type": "intraday_trade",
                        "status": "candidate_found",
                        "symbol": symbol,
                        "probability": prob,
                        "final_score": score.get("final_score", 0),
                    })

                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"랭킹 모니터링 오류: {e}")
                await asyncio.sleep(30)

    async def _scan_ai_candidates(self, limit: int = 5) -> list[dict]:
        """저장된 AI 모델 중 상위 후보 스캔 (간단화)"""
        from intraday_ai import IntradayAIEngine, MODEL_DIR
        from pathlib import Path

        candidates = []
        model_dir = MODEL_DIR
        if not model_dir.exists():
            return candidates

        for meta_path in model_dir.glob("*_intraday_meta.json"):
            if len(candidates) >= limit:
                break
            try:
                symbol = meta_path.name.replace("_intraday_meta.json", "")
                current = await self.kis.get_current_price(symbol)
                if not current or current.get("price", 0) <= 0:
                    continue

                engine = IntradayAIEngine()
                if not engine.load_model(symbol):
                    continue

                bars = await self.kis.get_intraday_ohlcv(symbol, 120)
                try:
                    orderbook = await self.kis.get_orderbook(symbol)
                except:
                    orderbook = {}

                score = engine.predict_latest(bars, orderbook=orderbook)
                if not score:
                    continue

                features = score.get("features", {})
                if (score["probability"] >= self.entry_threshold and
                    features.get("turnover_growth", 0) > 0 and
                    int(features.get("break_15m_high", 0)) == 1):
                    candidates.append({
                        "symbol": symbol,
                        "name": current.get("name", ""),
                        "price": current.get("price"),
                        "score": score,
                    })

                await asyncio.sleep(0.1)
            except Exception as e:
                logger.debug(f"후보 스캔 오류 {meta_path.name}: {e}")

        candidates.sort(key=lambda x: x.get("score", {}).get("final_score", 0), reverse=True)
        return candidates

    async def entry_signal(self, symbol: str) -> bool:
        """진입 신호 최종 확인"""
        try:
            engine = IntradayAIEngine()
            if not engine.load_model(symbol):
                return False

            bars = await self.kis.get_intraday_ohlcv(symbol, 120)
            try:
                orderbook = await self.kis.get_orderbook(symbol)
            except:
                orderbook = {}

            score = engine.predict_latest(bars, orderbook=orderbook)
            if not score:
                return False

            features = score.get("features", {})
            return (score["probability"] >= self.entry_threshold and
                    features.get("turnover_growth", 0) > 0 and
                    int(features.get("break_15m_high", 0)) == 1)
        except Exception as e:
            logger.debug(f"진입 신호 확인 실패 {symbol}: {e}")
            return False

    async def _manage_position_loop(self, broadcast: Callable):
        """포지션 관리 루프 (2초 마다 체크)"""
        while self._running:
            try:
                if self._position:
                    await self._check_exit_condition(broadcast)
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"포지션 관리 오류: {e}")
                await asyncio.sleep(2)

    async def _check_exit_condition(self, broadcast: Callable):
        """청산 조건 체크: 익절, 손절, 시간 한계"""
        if not self._position:
            return

        symbol = self._position.symbol
        try:
            current = await self.kis.get_current_price(symbol)
            price = current.get("price", 0)
            if price <= 0:
                return

            self._position.update_price(price)

            # 1. 익절 체크
            if self._position.pnl_pct >= self.take_profit * 100:
                await self._exit_position("take_profit", broadcast)
                return

            # 2. 손절 체크
            if self._position.pnl_pct <= self.stop_loss * 100:
                await self._exit_position("stop_loss", broadcast)
                return

            # 3. 시간 한계 체크
            if self._position.elapsed_minutes() >= self.time_exit_minutes:
                await self._exit_position("time_exit", broadcast)
                return

            # 4. 일일 손실 한계 체크
            if self._total_pnl <= self.max_daily_loss:
                await self._exit_position("daily_loss_limit", broadcast)
                return

            # 상태 브로드캐스트
            await broadcast({
                "type": "intraday_trade",
                "status": "position_update",
                "position": self._position.to_dict(),
            })

        except Exception as e:
            logger.error(f"청산 조건 체크 오류: {e}")

    async def _exit_position(self, reason: str, broadcast: Optional[Callable] = None):
        """포지션 청산"""
        if not self._position:
            return

        symbol = self._position.symbol
        qty = self._position.qty
        entry_price = self._position.entry_price

        try:
            current = await self.kis.get_current_price(symbol)
            exit_price = current.get("price", entry_price)

            result = await self.kis.place_order(symbol, "SELL", qty, price=0)

            net_return = (exit_price - entry_price) * qty
            self._total_pnl += net_return
            self._trades_closed += 1

            log_entry = {
                "time": datetime.now().isoformat(),
                "type": "exit",
                "symbol": symbol,
                "qty": qty,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "pnl": round(net_return, 2),
                "pnl_pct": round(self._position.pnl_pct, 2),
                "reason": reason,
                "elapsed_minutes": self._position.elapsed_minutes(),
            }
            self._log.append(log_entry)

            logger.info(f"포지션 청산: {symbol} {qty}주 @ {exit_price} ({reason}) | PnL: ₩{net_return:,.0f}")

            if broadcast:
                await broadcast({
                    "type": "intraday_trade",
                    "status": "position_closed",
                    "reason": reason,
                    "data": log_entry,
                    "total_pnl": round(self._total_pnl, 2),
                    "trades_closed": self._trades_closed,
                })

            self._position = None

        except Exception as e:
            logger.error(f"청산 오류 {symbol}: {e}")

    async def entry_position(self, symbol: str, qty: int, broadcast: Callable):
        """포지션 진입"""
        if self._position:
            logger.warning(f"이미 포지션 보유 중: {self._position.symbol}")
            return False

        if qty <= 0 or qty > self.max_position_qty:
            logger.warning(f"잘못된 수량: {qty}")
            return False

        try:
            current = await self.kis.get_current_price(symbol)
            entry_price = current.get("price", 0)
            if entry_price <= 0:
                logger.warning(f"유효하지 않은 가격: {symbol}")
                return False

            # 재확인: 진입 신호 유효성
            if not await self.entry_signal(symbol):
                logger.info(f"진입 신호 재확인 실패: {symbol}")
                return False

            result = await self.kis.place_order(symbol, "BUY", qty, price=0)

            self._position = Position(symbol, entry_price, qty, datetime.now())

            log_entry = {
                "time": datetime.now().isoformat(),
                "type": "entry",
                "symbol": symbol,
                "name": current.get("name", ""),
                "qty": qty,
                "entry_price": round(entry_price, 2),
            }
            self._log.append(log_entry)

            logger.info(f"진입: {symbol} {qty}주 @ {entry_price}")

            await broadcast({
                "type": "intraday_trade",
                "status": "position_opened",
                "data": log_entry,
            })

            return True

        except Exception as e:
            logger.error(f"진입 오류 {symbol}: {e}")
            return False

    def status(self) -> dict:
        """현재 상태 반환"""
        return {
            "running": self._running,
            "has_position": self._position is not None,
            "position": self._position.to_dict() if self._position else None,
            "total_pnl": round(self._total_pnl, 2),
            "trades_closed": self._trades_closed,
            "config": {
                "entry_threshold": self.entry_threshold,
                "take_profit": self.take_profit,
                "stop_loss": self.stop_loss,
                "time_exit_minutes": self.time_exit_minutes,
                "max_daily_loss": self.max_daily_loss,
            },
        }

    def get_log(self) -> list:
        """거래 로그 반환"""
        return list(reversed(self._log[-100:]))
