"""
한국투자증권 OpenAPI 자동매매 백엔드
FastAPI + WebSocket 실시간 시세
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import asyncio
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
import os
import argparse
from pathlib import Path

def resolve_env_file(profile: str) -> Path:
    """
    profile → 로드할 .env 파일 경로
      default  → .env
      local    → .env.local
      dev      → .env.dev
      prod     → .env.prod
      (임의 이름도 가능: .env.{profile})
    """
    base = Path(__file__).parent
    if profile == "default":
        path = base / ".env"
    else:
        path = base / f".env.{profile}"
    return path

# ── 실행 시 프로파일 파싱 (uvicorn 직접 실행 시에도 동작) ──
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--env", default=os.getenv("KIS_ENV_PROFILE", "default"),
                     help="환경 프로파일 (default|local|dev|prod|...)")
_args, _ = _parser.parse_known_args()
ENV_PROFILE = _args.env

_env_path = resolve_env_file(ENV_PROFILE)
if _env_path.exists():
    load_dotenv(_env_path, override=True)
    _loaded = True
else:
    _loaded = False
    print(f"⚠️  환경파일 없음: {_env_path}")

from kis_api import KISApi
from strategy import STRATEGY_MAP, GoldenCrossStrategy, RSIStrategy, BollingerStrategy, MACDStrategy
from strategy import AutoTrader
from screener import Screener, ScreenerCondition, ALL_STOCKS
from stock_master import stock_master, schedule_daily_refresh
from lgbm_optimizer import LGBMOptimizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 프로파일 로그
if _loaded:
    logger.info(f"🌿 환경 프로파일: [{ENV_PROFILE}] ({_env_path})")
else:
    logger.warning(f"⚠️  환경파일 없음: {_env_path} — 수동 인증 필요")

app = FastAPI(title="KIS 자동매매 시스템")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 전역 상태 ──────────────────────────────────────────────
kis: Optional[KISApi] = None
traders: dict[str, AutoTrader] = {}
ws_clients: List[WebSocket] = []
screener_instance: Optional[Screener] = None
lgbm_optimizer:    Optional[LGBMOptimizer] = None
lgbm_batch_state: dict = {"running": False, "stop": False}

# ── .env에서 자격증명 로드 (모의/실전 분리) ─────────────────────
def load_credentials_from_env(mode: str = "mock") -> Optional[dict]:
    """
    mode = "mock" → KIS_MOCK_* 접두사 키 사용
    mode = "real" → KIS_REAL_* 접두사 키 사용
    """
    prefix = "MOCK" if mode == "mock" else "REAL"
    key    = os.getenv(f"KIS_{prefix}_APP_KEY")
    secret = os.getenv(f"KIS_{prefix}_APP_SECRET")
    acct   = os.getenv(f"KIS_{prefix}_ACCOUNT_NO")
    is_mock = (mode == "mock")
    if key and secret and acct:
        return {"app_key": key, "app_secret": secret,
                "account_no": acct, "is_mock": is_mock}
    return None

def get_env_summary() -> dict:
    """프론트에 내려줄 .env 설정 요약 (키값 마스킹)"""
    result = {}
    for mode in ("mock", "real"):
        creds = load_credentials_from_env(mode)
        if creds:
            key = creds["app_key"]
            result[mode] = {
                "configured": True,
                "account_no": creds["account_no"],
                "app_key_preview": key[:6] + "****" + key[-4:] if len(key) > 10 else "****",
            }
        else:
            result[mode] = {"configured": False, "account_no": None, "app_key_preview": None}
    return result

# ── 요청/응답 모델 ──────────────────────────────────────────
class AuthRequest(BaseModel):
    app_key: str
    app_secret: str
    account_no: str        # 계좌번호 (예: 50123456-01)
    is_mock: bool = True   # 모의투자 여부

class OrderRequest(BaseModel):
    symbol: str
    side: str              # BUY / SELL
    qty: int
    price: int = 0         # 0 = 시장가

class StrategyRequest(BaseModel):
    strategy: str = "golden_cross"  # golden_cross | rsi | bollinger | macd | lgbm_ai
    symbol: str
    qty: int
    check_interval: int = 60        # 초
    # 골든크로스
    short_period: int = 5
    long_period: int = 20
    # RSI
    rsi_period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0
    # 볼린저밴드
    bb_period: int = 20
    bb_k: float = 2.0
    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # AI 파라미터
    ai_confidence: float = 0.45

# ── 인증 ───────────────────────────────────────────────────
@app.post("/api/auth")
async def authenticate(req: AuthRequest):
    global kis
    try:
        await stop_all_traders()
        kis = KISApi(
            app_key=req.app_key,
            app_secret=req.app_secret,
            account_no=req.account_no,
            is_mock=req.is_mock,
        )
        token = await kis.get_access_token()
        return {"ok": True, "message": "인증 성공", "token_expires": token["access_token_token_expired"]}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

def require_auth():
    if kis is None:
        raise HTTPException(status_code=401, detail="먼저 인증이 필요합니다")

async def stop_all_traders():
    for symbol, trader in list(traders.items()):
        try:
            await trader.stop()
        except Exception as e:
            logger.warning(f"{symbol} 자동매매 중지 실패: {e}")
    traders.clear()

def build_strategy_from_request(req: StrategyRequest):
    strat_cls = STRATEGY_MAP.get(req.strategy)
    if strat_cls is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 전략: {req.strategy}")

    if req.strategy == "golden_cross":
        return strat_cls(symbol=req.symbol, qty=req.qty,
                         short_period=req.short_period, long_period=req.long_period)
    if req.strategy == "rsi":
        return strat_cls(symbol=req.symbol, qty=req.qty,
                         period=req.rsi_period, oversold=req.oversold, overbought=req.overbought)
    if req.strategy == "bollinger":
        return strat_cls(symbol=req.symbol, qty=req.qty,
                         period=req.bb_period, k=req.bb_k)
    if req.strategy == "macd":
        return strat_cls(symbol=req.symbol, qty=req.qty,
                         fast=req.macd_fast, slow=req.macd_slow, signal_period=req.macd_signal)
    if req.strategy == "lgbm_ai":
        return strat_cls(symbol=req.symbol, qty=req.qty, kis_api=kis,
                         min_confidence=req.ai_confidence)
    raise HTTPException(status_code=400, detail="지원하지 않는 전략")

# ── 종목 검색 (전체 종목 인메모리 검색) ────────────────────────
@app.get("/api/search")
async def search_stock(q: str):
    q = q.strip()
    if not q:
        return []
    # 마스터가 아직 로딩 중이면 대기
    await stock_master.ensure_loaded()
    results = stock_master.search(q, limit=20)
    return results

@app.get("/api/master/status")
async def master_status():
    """종목 마스터 로드 상태"""
    return {
        "loaded": stock_master.loaded,
        "total": stock_master.total,
        "loaded_date": str(stock_master._loaded_date) if stock_master._loaded_date else None,
    }

# ── 시세 조회 ───────────────────────────────────────────────
@app.get("/api/price/{symbol}")
async def get_price(symbol: str):
    require_auth()
    data = await kis.get_current_price(symbol)
    return data

@app.get("/api/price/{symbol}/history")
async def get_history(symbol: str, period: str = "D", count: int = 60):
    require_auth()
    data = await kis.get_ohlcv(symbol, period, count)
    return data

# ── 잔고 / 계좌 ─────────────────────────────────────────────
@app.get("/api/balance")
async def get_balance():
    require_auth()
    data = await kis.get_balance()
    return data

@app.get("/api/positions")
async def get_positions():
    require_auth()
    data = await kis.get_positions()
    return data

# ── 주문 ───────────────────────────────────────────────────
@app.post("/api/order")
async def place_order(req: OrderRequest):
    require_auth()
    result = await kis.place_order(
        symbol=req.symbol,
        side=req.side,
        qty=req.qty,
        price=req.price,
    )
    await broadcast({"type": "order", "data": result})
    return result

@app.get("/api/orders")
async def get_orders():
    require_auth()
    data = await kis.get_orders()
    return data

# ── 자동매매 전략 ───────────────────────────────────────────
@app.post("/api/strategy/start")
async def start_strategy(req: StrategyRequest):
    require_auth()
    symbol = req.symbol.strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="종목코드가 필요합니다")
    req.symbol = symbol

    strategy = build_strategy_from_request(req)
    trader = traders.get(symbol)
    if trader is None:
        trader = AutoTrader(kis)
        traders[symbol] = trader

    await trader.start(strategy, req.check_interval, broadcast)
    return {
        "ok": True,
        "message": f"{symbol} {strategy.name} 자동매매 시작",
        "symbol": symbol,
        "active_count": sum(1 for t in traders.values() if t.status().get("running")),
    }

@app.get("/api/strategy/list")
async def list_strategies():
    return [
        {"id": "golden_cross", "name": "골든크로스",  "desc": "단기/장기 이동평균 크로스"},
        {"id": "rsi",          "name": "RSI",         "desc": "RSI 과매수/과매도"},
        {"id": "bollinger",    "name": "볼린저밴드",  "desc": "볼린저밴드 상/하단 돌파"},
        {"id": "macd",         "name": "MACD",        "desc": "MACD 시그널 크로스"},
        {"id": "lgbm_ai",      "name": "AI 파라미터", "desc": "저장된 LightGBM 모델 예측"},
    ]

@app.post("/api/strategy/stop")
async def stop_strategy():
    stopped = len(traders)
    await stop_all_traders()
    return {"ok": True, "message": "자동매매 전체 중지", "stopped": stopped}

@app.post("/api/strategy/stop/{symbol}")
async def stop_strategy_symbol(symbol: str):
    trader = traders.pop(symbol, None)
    if trader is None:
        raise HTTPException(status_code=404, detail=f"{symbol} 실행 중인 자동매매가 없습니다")
    await trader.stop()
    return {"ok": True, "message": f"{symbol} 자동매매 중지", "symbol": symbol}

@app.get("/api/strategy/status")
async def strategy_status():
    statuses = [trader.status() for trader in traders.values()]
    running_statuses = [s for s in statuses if s.get("running")]
    primary = running_statuses[0] if running_statuses else (statuses[0] if statuses else {})
    return {
        **primary,
        "running": bool(running_statuses),
        "traders": statuses,
        "active_count": len(running_statuses),
        "total_count": len(statuses),
    }

@app.get("/api/strategy/log")
async def strategy_log():
    logs = []
    for trader in traders.values():
        logs.extend(trader.get_log())
    logs.sort(key=lambda item: item.get("time", ""), reverse=True)
    return logs[:300]

# ── WebSocket 브로드캐스트 ──────────────────────────────────
async def broadcast(message: dict):
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            # 클라이언트 ping 유지
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_clients.remove(websocket)

# ── 실시간 시세 WebSocket (클라이언트에서 종목 구독) ─────────
@app.websocket("/ws/price/{symbol}")
async def price_ws(websocket: WebSocket, symbol: str):
    await websocket.accept()
    try:
        while True:
            if kis:
                price_data = await kis.get_current_price(symbol)
                await websocket.send_json({"type": "price", "symbol": symbol, "data": price_data})
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass

@app.on_event("startup")
async def auto_auth_from_env():
    """서버 시작 시 .env 자동 인증 + 종목 마스터 로드"""
    global kis

    # 종목 마스터 백그라운드 로드 (인증과 병렬)
    asyncio.create_task(stock_master.ensure_loaded())
    asyncio.create_task(schedule_daily_refresh())

    # .env 자동 인증
    creds = load_credentials_from_env("mock") or load_credentials_from_env("real")
    if creds:
        try:
            kis = KISApi(**creds)
            await kis.get_access_token()
            mode_label = "모의투자" if creds["is_mock"] else "실전투자"
            logger.info(f"✅ .env 자동 인증 완료 ({mode_label} / {creds['account_no']})")
        except Exception as e:
            logger.warning(f"⚠️  .env 자동 인증 실패: {e}")
    else:
        logger.info("ℹ️  .env 미설정 — 대시보드에서 수동 입력 필요")

# ── .env 상태 조회 API ──────────────────────────────────────
@app.get("/api/env-status")
async def env_status():
    """모의/실전 계좌 설정 상태 + 현재 인증 모드 + 프로파일 반환"""
    summary = get_env_summary()
    current_mode = None
    if kis is not None:
        current_mode = "mock" if kis.is_mock else "real"
    return {
        "mock": summary["mock"],
        "real": summary["real"],
        "current_mode": current_mode,
        "already_authed": kis is not None,
        "profile": ENV_PROFILE,
        "env_file": str(_env_path),
        "env_loaded": _loaded,
    }

# ── .env 계좌로 전환 (모의 ↔ 실전) ─────────────────────────
class SwitchModeRequest(BaseModel):
    mode: str  # "mock" | "real"

@app.post("/api/switch-mode")
async def switch_mode(req: SwitchModeRequest):
    global kis
    if req.mode not in ("mock", "real"):
        raise HTTPException(status_code=400, detail="mode는 mock 또는 real 이어야 합니다")
    creds = load_credentials_from_env(req.mode)
    if not creds:
        raise HTTPException(
            status_code=404,
            detail=f".env에 {'모의투자' if req.mode == 'mock' else '실전투자'} 계좌 설정이 없습니다"
        )
    try:
        await stop_all_traders()
        kis = KISApi(**creds)
        await kis.get_access_token()
        mode_label = "모의투자" if creds["is_mock"] else "실전투자"
        logger.info(f"🔄 계좌 전환: {mode_label} ({creds['account_no']})")
        return {
            "ok": True,
            "mode": req.mode,
            "account_no": creds["account_no"],
            "message": f"{mode_label} 계좌로 전환됐습니다"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    import sys
    # reload=True 일 때 argparse가 uvicorn 인수와 충돌하지 않도록
    # --env 인수는 이미 위에서 파싱 완료
    logger.info(f"🚀 서버 시작 | 프로파일: [{ENV_PROFILE}] | http://0.0.0.0:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

# ── 스크리너 ────────────────────────────────────────────────
class ScreenerRequest(BaseModel):
    universe: str = "all"           # all | kospi200 | kosdaq150
    # RSI
    use_rsi: bool = False
    rsi_period: int = 14
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    # 볼린저밴드
    use_bollinger: bool = False
    bb_period: int = 20
    bb_k: float = 2.0
    bb_position: str = "below_lower"
    # MACD
    use_macd: bool = False
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_cross: str = "golden"
    # 이동평균 크로스
    use_ma_cross: bool = False
    ma_short: int = 5
    ma_long: int = 20
    ma_cross: str = "golden"
    # 거래량 급증
    use_volume: bool = False
    volume_ratio: float = 2.0
    volume_avg_days: int = 20
    # 등락률
    use_change: bool = False
    change_min: Optional[float] = None
    change_max: Optional[float] = None
    # 추세/모멘텀
    use_near_high: bool = False
    high_days: int = 20
    high_within_pct: float = 3.0
    use_above_ma60: bool = False
    # 수급/실적
    use_foreign: bool = False
    foreign_days: int = 5
    use_fundamental: bool = False
    growth_metric: str = "any"
    growth_min: float = 0.0
    # AI 저장 모델
    use_ai: bool = False
    ai_signal: str = "BUY"
    ai_min_prob: float = 0.45

@app.post("/api/screener/run")
async def run_screener(req: ScreenerRequest):
    global screener_instance
    require_auth()
    if screener_instance and screener_instance._running:
        screener_instance.stop()

    screener_instance = Screener(kis)
    cond = ScreenerCondition(
        use_rsi=req.use_rsi, rsi_period=req.rsi_period,
        rsi_min=req.rsi_min, rsi_max=req.rsi_max,
        use_bollinger=req.use_bollinger, bb_period=req.bb_period,
        bb_k=req.bb_k, bb_position=req.bb_position,
        use_macd=req.use_macd, macd_fast=req.macd_fast,
        macd_slow=req.macd_slow, macd_signal=req.macd_signal,
        macd_cross=req.macd_cross,
        use_ma_cross=req.use_ma_cross, ma_short=req.ma_short,
        ma_long=req.ma_long, ma_cross=req.ma_cross,
        use_volume=req.use_volume, volume_ratio=req.volume_ratio,
        volume_avg_days=req.volume_avg_days,
        use_change=req.use_change, change_min=req.change_min,
        change_max=req.change_max,
        use_near_high=req.use_near_high, high_days=req.high_days,
        high_within_pct=req.high_within_pct, use_above_ma60=req.use_above_ma60,
        use_foreign=req.use_foreign, foreign_days=req.foreign_days,
        use_fundamental=req.use_fundamental, growth_metric=req.growth_metric,
        growth_min=req.growth_min,
        use_ai=req.use_ai, ai_signal=req.ai_signal, ai_min_prob=req.ai_min_prob,
    )
    targets_override = None
    if req.universe == "all":
        await stock_master.ensure_loaded()
        if stock_master.loaded:
            targets_override = {
                s["symbol"]: s["name"]
                for s in stock_master.all_stocks()
                if s.get("symbol") and s.get("name")
            }
    if req.use_ai:
        model_dir = Path(__file__).parent / "models"
        ai_symbols = {
            p.name.replace("_meta.json", "")
            for p in model_dir.glob("*_meta.json")
            if (model_dir / p.name.replace("_meta.json", "_lgbm.txt")).exists()
        }
        if targets_override is None:
            base_targets = (
                {s[0]: s[1] for s in __import__("screener").KOSPI200}
                if req.universe == "kospi200"
                else {s[0]: s[1] for s in __import__("screener").KOSDAQ150}
                if req.universe == "kosdaq150"
                else dict(__import__("screener").ALL_STOCKS)
            )
            targets_override = base_targets
        targets_override = {
            symbol: name for symbol, name in targets_override.items()
            if symbol in ai_symbols
        }

    # 비동기 백그라운드 실행
    asyncio.create_task(screener_instance.run(cond, req.universe, broadcast, targets_override))
    if targets_override is not None:
        total = len(targets_override)
    else:
        total = len([s for s in (
            list(__import__("screener").KOSPI200) if req.universe == "kospi200"
            else list(__import__("screener").KOSDAQ150) if req.universe == "kosdaq150"
            else list(__import__("screener").ALL_STOCKS.items())
        )])
    return {"ok": True, "message": "스크리닝 시작", "total": total}

@app.post("/api/screener/stop")
async def stop_screener():
    global screener_instance
    if screener_instance:
        screener_instance.stop()
    return {"ok": True}

@app.get("/api/screener/progress")
async def screener_progress():
    if screener_instance is None:
        return {"total": 0, "done": 0, "status": "idle", "pct": 0}
    p = screener_instance.progress()
    p["pct"] = round(p["done"] / p["total"] * 100) if p["total"] else 0
    return p

@app.get("/api/screener/result")
async def screener_result():
    if screener_instance is None:
        return []
    return screener_instance.last_result()

# ── LightGBM 최적화 ─────────────────────────────────────────
class LGBMRequest(BaseModel):
    symbol:   str
    n_trials: int = 50
    ohlcv_count: int = 500

class LGBMBatchRequest(BaseModel):
    n_trials: int = 50
    ohlcv_count: int = 500

@app.post("/api/lgbm/run")
async def lgbm_run(req: LGBMRequest):
    global lgbm_optimizer
    require_auth()
    if lgbm_batch_state.get("running"):
        raise HTTPException(status_code=400, detail="이미 저장 모델 전체 재분석이 실행 중입니다")
    if lgbm_optimizer and lgbm_optimizer.is_running:
        raise HTTPException(status_code=400, detail="이미 최적화가 실행 중입니다")
    lgbm_optimizer = LGBMOptimizer(kis)
    asyncio.create_task(
        lgbm_optimizer.run(req.symbol, req.n_trials, req.ohlcv_count, broadcast)
    )
    return {"ok": True, "message": f"{req.symbol} LightGBM 최적화 시작",
            "n_trials": req.n_trials, "ohlcv_count": req.ohlcv_count}

@app.post("/api/lgbm/stop")
async def lgbm_stop():
    lgbm_batch_state["stop"] = True
    if lgbm_optimizer:
        lgbm_optimizer.stop()
    return {"ok": True}

@app.get("/api/lgbm/progress")
async def lgbm_progress():
    if lgbm_batch_state.get("running"):
        current = lgbm_optimizer.progress.copy() if lgbm_optimizer else {}
        return {
            **current,
            "status": lgbm_batch_state.get("status", current.get("status", "running")),
            "message": lgbm_batch_state.get("message", current.get("message", "")),
            "batch_index": lgbm_batch_state.get("index", 0),
            "batch_total": lgbm_batch_state.get("total", 0),
            "batch_done": lgbm_batch_state.get("done", 0),
            "batch_failed": lgbm_batch_state.get("failed", 0),
            "symbol": lgbm_batch_state.get("symbol", current.get("symbol")),
        }
    if lgbm_optimizer is None:
        return {"status": "idle", "trial": 0, "total": 0, "best_score": 0}
    return lgbm_optimizer.progress

@app.get("/api/lgbm/result")
async def lgbm_result():
    if lgbm_optimizer is None:
        return {"status": "idle"}
    return lgbm_optimizer.result or {"status": "idle"}

@app.get("/api/lgbm/models")
async def lgbm_models(include_prediction: bool = False):
    models = _saved_lgbm_models()
    if include_prediction and kis is not None:
        await _attach_lgbm_predictions(models)
    elif include_prediction:
        for model in models:
            model["prediction_status"] = "auth_required"
    return models

def _saved_lgbm_models():
    model_dir = Path(__file__).parent / "models"
    if not model_dir.exists():
        return []

    models = []
    for meta_path in model_dir.glob("*_meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
            symbol = meta.get("symbol") or meta_path.name.replace("_meta.json", "")
            stock = stock_master.get_by_code(symbol) if stock_master.loaded else None
            params = meta.get("best_params") or {}
            models.append({
                "symbol": symbol,
                "name": stock["name"] if stock else ALL_STOCKS.get(symbol, ""),
                "trained_at": meta.get("trained_at"),
                "feature_count": len(meta.get("feature_cols") or []),
                "horizon": meta.get("horizon") or params.get("horizon"),
                "buy_threshold": meta.get("buy_threshold") or params.get("buy_threshold"),
                "sell_threshold": meta.get("sell_threshold") or params.get("sell_threshold"),
                "has_model": (model_dir / f"{symbol}_lgbm.txt").exists(),
            })
        except Exception as e:
            logger.warning(f"LGBM 모델 메타 읽기 실패 {meta_path}: {e}")

    models.sort(key=lambda m: m.get("trained_at") or "", reverse=True)
    return models

async def _attach_lgbm_predictions(models: list[dict]):
    for model in models:
        if not model.get("has_model"):
            model["prediction_status"] = "missing_model"
            continue

        symbol = model.get("symbol")
        try:
            model.update(await _predict_saved_lgbm_model(symbol))
        except Exception as e:
            logger.warning(f"LGBM 현재 예측 실패 {symbol}: {e}")
            model["prediction_status"] = "error"
            model["prediction_error"] = str(e)

async def _predict_saved_lgbm_model(symbol: str) -> dict:
    require_auth()
    optimizer = LGBMOptimizer(kis)
    if not optimizer.load_model(symbol):
        return {"prediction_status": "load_failed"}

    ohlcv = await kis.get_ohlcv(symbol, "D", 200)
    pred = optimizer.predict_latest(ohlcv)
    if pred is None:
        return {"prediction_status": "predict_failed"}

    return {"prediction_status": "ok", "prediction": pred}

@app.get("/api/lgbm/saved-predict/{symbol}")
async def lgbm_saved_predict(symbol: str):
    return await _predict_saved_lgbm_model(symbol)

@app.post("/api/lgbm/rerun-saved")
async def lgbm_rerun_saved(req: LGBMBatchRequest):
    global lgbm_optimizer
    require_auth()
    if lgbm_batch_state.get("running"):
        raise HTTPException(status_code=400, detail="이미 저장 모델 전체 재분석이 실행 중입니다")
    if lgbm_optimizer and lgbm_optimizer.is_running:
        raise HTTPException(status_code=400, detail="이미 최적화가 실행 중입니다")

    symbols = [m["symbol"] for m in _saved_lgbm_models() if m.get("has_model")]
    if not symbols:
        raise HTTPException(status_code=404, detail="재분석할 저장 모델이 없습니다")

    lgbm_batch_state.clear()
    lgbm_batch_state.update({
        "running": True,
        "stop": False,
        "status": "running",
        "message": f"저장 모델 전체 재분석 시작 (총 {len(symbols)}개)",
        "index": 0,
        "total": len(symbols),
        "done": 0,
        "failed": 0,
        "symbol": None,
        "last_symbol": None,
        "errors": [],
    })

    asyncio.create_task(_run_saved_lgbm_batch(symbols, req.n_trials, req.ohlcv_count))
    return {
        "ok": True,
        "message": f"저장 모델 {len(symbols)}개 재분석 시작",
        "symbols": symbols,
        "n_trials": req.n_trials,
        "ohlcv_count": req.ohlcv_count,
    }

async def _run_saved_lgbm_batch(symbols: list[str], n_trials: int, ohlcv_count: int):
    global lgbm_optimizer
    total = len(symbols)
    last_symbol = None

    try:
        for idx, symbol in enumerate(symbols, start=1):
            if lgbm_batch_state.get("stop"):
                break

            lgbm_batch_state.update({
                "index": idx,
                "symbol": symbol,
                "status": "running",
                "message": f"[{idx}/{total}] {symbol} 재분석 중...",
            })
            await broadcast({
                "type": "lgbm",
                "status": "batch",
                "message": lgbm_batch_state["message"],
                "progress": {
                    "status": "batch",
                    "trial": 0,
                    "total": n_trials,
                    "best_score": 0,
                    "symbol": symbol,
                    "batch_index": idx,
                    "batch_total": total,
                    "batch_done": lgbm_batch_state["done"],
                    "batch_failed": lgbm_batch_state["failed"],
                },
            })

            async def batch_broadcast(message: dict):
                msg = message.copy()
                progress = (msg.get("progress") or {}).copy()
                progress.update({
                    "batch_index": idx,
                    "batch_total": total,
                    "batch_done": lgbm_batch_state.get("done", 0),
                    "batch_failed": lgbm_batch_state.get("failed", 0),
                    "symbol": symbol,
                })
                msg["progress"] = progress
                msg["message"] = f"[{idx}/{total}] {msg.get('message', '')}"
                if msg.get("status") == "done" and idx < total:
                    msg["status"] = "batch_item_done"
                await broadcast(msg)

            lgbm_optimizer = LGBMOptimizer(kis)
            await lgbm_optimizer.run(symbol, n_trials, ohlcv_count, batch_broadcast)

            result = lgbm_optimizer.result or {}
            if result.get("status") == "done":
                lgbm_batch_state["done"] += 1
                last_symbol = symbol
                lgbm_batch_state["last_symbol"] = symbol
            else:
                lgbm_batch_state["failed"] += 1
                lgbm_batch_state["errors"].append({
                    "symbol": symbol,
                    "message": result.get("message", "알 수 없는 오류"),
                })

        stopped = lgbm_batch_state.get("stop")
        status = "idle" if stopped else "done"
        message = (
            f"저장 모델 전체 재분석 중지 · 완료 {lgbm_batch_state['done']}개 / 실패 {lgbm_batch_state['failed']}개"
            if stopped else
            f"저장 모델 전체 재분석 완료 · 완료 {lgbm_batch_state['done']}개 / 실패 {lgbm_batch_state['failed']}개"
        )
        lgbm_batch_state.update({"status": status, "message": message})
        await broadcast({
            "type": "lgbm",
            "status": "done" if not stopped else "idle",
            "message": message,
            "progress": {
                "status": status,
                "trial": n_trials,
                "total": n_trials,
                "best_score": (lgbm_optimizer.progress or {}).get("best_score", 0) if lgbm_optimizer else 0,
                "symbol": last_symbol,
                "batch_index": lgbm_batch_state.get("index", 0),
                "batch_total": total,
                "batch_done": lgbm_batch_state["done"],
                "batch_failed": lgbm_batch_state["failed"],
            },
        })
    except Exception as e:
        logger.error(f"LGBM 저장 모델 전체 재분석 오류: {e}", exc_info=True)
        lgbm_batch_state.update({"status": "error", "message": f"전체 재분석 오류: {e}"})
        await broadcast({
            "type": "lgbm",
            "status": "error",
            "message": lgbm_batch_state["message"],
            "progress": {
                "status": "error",
                "trial": 0,
                "total": n_trials,
                "best_score": 0,
                "symbol": lgbm_batch_state.get("symbol"),
                "batch_index": lgbm_batch_state.get("index", 0),
                "batch_total": total,
                "batch_done": lgbm_batch_state.get("done", 0),
                "batch_failed": lgbm_batch_state.get("failed", 0),
            },
        })
    finally:
        lgbm_batch_state["running"] = False

@app.get("/api/lgbm/predict/{symbol}")
async def lgbm_predict(symbol: str):
    require_auth()
    if lgbm_optimizer is None or lgbm_optimizer.result is None:
        raise HTTPException(status_code=404, detail="학습된 모델이 없습니다")
    ohlcv = await kis.get_ohlcv(symbol, "D", 200)
    pred  = lgbm_optimizer.predict_latest(ohlcv)
    if pred is None:
        raise HTTPException(status_code=500, detail="예측 실패")
    return {**pred, "symbol": symbol}

@app.post("/api/lgbm/load/{symbol}")
async def lgbm_load(symbol: str):
    global lgbm_optimizer
    require_auth()
    lgbm_optimizer = LGBMOptimizer(kis)
    ok = lgbm_optimizer.load_model(symbol)
    if not ok:
        raise HTTPException(status_code=404, detail=f"{symbol} 저장된 모델 없음")
    return {"ok": True, "result": lgbm_optimizer.result}
