"""
종목 스크리너 (screener.py)
KOSPI200 / KOSDAQ150 종목을 대상으로
RSI, 볼린저밴드, MACD, 골든크로스, 거래량급증, 등락률 조건 필터링
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional
from strategy import _sma, _ema, _stddev
from lgbm_optimizer import LGBMOptimizer

logger = logging.getLogger(__name__)

# ── KOSPI200 + KOSDAQ150 대표 종목 ──────────────────────────
# 실제 운영 시 KIS API의 업종/지수 구성종목 조회로 교체 가능
KOSPI200 = [
    ("005930","삼성전자"),("000660","SK하이닉스"),("005380","현대차"),
    ("035420","NAVER"),("000270","기아"),("068270","셀트리온"),
    ("105560","KB금융"),("055550","신한지주"),("003550","LG"),
    ("096770","SK이노베이션"),("034730","SK"),("012330","현대모비스"),
    ("066570","LG전자"),("028260","삼성물산"),("009150","삼성전기"),
    ("051910","LG화학"),("006400","삼성SDI"),("035720","카카오"),
    ("000810","삼성화재"),("032830","삼성생명"),("086790","하나금융지주"),
    ("010950","S-Oil"),("018260","삼성에스디에스"),("011200","HMM"),
    ("017670","SK텔레콤"),("030200","KT"),("015760","한국전력"),
    ("033780","KT&G"),("002790","아모레퍼시픽"),("011170","롯데케미칼"),
    ("071050","한국금융지주"),("024110","기업은행"),("000100","유한양행"),
    ("003490","대한항공"),("010130","고려아연"),("004020","현대제철"),
    ("097950","CJ제일제당"),("007070","GS리테일"),("139480","이마트"),
    ("009540","HD한국조선해양"),("042660","한화오션"),("329180","HD현대중공업"),
]

KOSDAQ150 = [
    ("247540","에코프로비엠"),("086520","에코프로"),("196170","알테오젠"),
    ("031330","에스씨엔씨"),("357780","솔브레인"),("112040","위메이드"),
    ("263750","펄어비스"),("293490","카카오게임즈"),("251270","넷마블"),
    ("036030","오스템임플란트"),("214150","클래시스"),("145020","휴젤"),
    ("178920","PI첨단소재"),("091990","셀트리온헬스케어"),("272210","한화시스템"),
    ("090150","만도"),("041510","에스엠"),("035900","JYP Ent."),
    ("122870","와이지엔터테인먼트"),("095340","ISC"),("352820","하이브"),
    ("240810","원익IPS"),("058470","리노공업"),("036810","에프에스티"),
    ("054780","이오테크닉스"),("039030","이오테크닉스"),("950130","엑스페릭스"),
]

ALL_STOCKS = {s[0]: s[1] for s in KOSPI200 + KOSDAQ150}


# ── 스크리닝 조건 정의 ────────────────────────────────────────
@dataclass
class ScreenerCondition:
    # RSI
    use_rsi: bool = False
    rsi_period: int = 14
    rsi_min: Optional[float] = None   # RSI 이 값 이하
    rsi_max: Optional[float] = None   # RSI 이 값 이상

    # 볼린저밴드
    use_bollinger: bool = False
    bb_period: int = 20
    bb_k: float = 2.0
    bb_position: str = "below_lower"  # below_lower | above_upper | inside

    # MACD
    use_macd: bool = False
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_cross: str = "golden"        # golden | dead

    # 골든/데드크로스
    use_ma_cross: bool = False
    ma_short: int = 5
    ma_long: int = 20
    ma_cross: str = "golden"          # golden | dead

    # 거래량 급증
    use_volume: bool = False
    volume_ratio: float = 2.0         # 평균 대비 몇 배 이상
    volume_avg_days: int = 20

    # 등락률
    use_change: bool = False
    change_min: Optional[float] = None   # % 이상
    change_max: Optional[float] = None   # % 이하

    # 추세/모멘텀
    use_near_high: bool = False
    high_days: int = 20
    high_within_pct: float = 3.0
    use_above_ma60: bool = False

    # 수급/실적
    use_foreign: bool = False
    foreign_days: int = 5
    use_fundamental: bool = False
    growth_metric: str = "any"           # any | sales | operating | net
    growth_min: float = 0.0

    # AI 저장 모델
    use_ai: bool = False
    ai_signal: str = "BUY"               # BUY | SELL | HOLD | ANY
    ai_min_prob: float = 0.45             # 0.0 ~ 1.0


# ── 지표 계산 ─────────────────────────────────────────────────
def calc_rsi(closes: list, period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent = deltas[-period:]
    gains  = [d if d > 0 else 0 for d in recent]
    losses = [-d if d < 0 else 0 for d in recent]
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_bollinger(closes: list, period: int, k: float):
    ma  = _sma(closes, period)
    std = _stddev(closes, period)
    if ma is None or std is None:
        return None, None, None
    return round(ma + k * std), round(ma), round(ma - k * std)

def calc_macd(closes: list, fast: int, slow: int, sig_period: int):
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    if ef is None or es is None:
        return None, None, None
    macd = ef - es
    # 시그널: 간이 EMA (마지막 값 기준)
    k = 2 / (sig_period + 1)
    signal = macd   # 초기값
    return round(macd, 2), round(signal, 2), round(macd - signal, 2)

def calc_ma_cross(closes: list, short: int, long_: int):
    if len(closes) < long_ + 1:
        return None
    prev_short = _sma(closes[:-1], short)
    prev_long  = _sma(closes[:-1], long_)
    curr_short = _sma(closes, short)
    curr_long  = _sma(closes, long_)
    if None in (prev_short, prev_long, curr_short, curr_long):
        return None
    if prev_short <= prev_long and curr_short > curr_long:
        return "golden"
    if prev_short >= prev_long and curr_short < curr_long:
        return "dead"
    return "none"


# ── 단일 종목 스크리닝 ────────────────────────────────────────
def screen_stock(symbol: str, name: str, ohlcv: list, current: dict,
                 cond: ScreenerCondition) -> Optional[dict]:
    if not ohlcv or len(ohlcv) < 30:
        return None

    closes  = [r["close"]  for r in ohlcv]
    volumes = [r["volume"] for r in ohlcv]
    price   = current["price"]
    change_pct = current["change_pct"]
    matched_conditions = []
    indicators = {}

    # ── RSI ──
    if cond.use_rsi:
        rsi = calc_rsi(closes, cond.rsi_period)
        if rsi is None:
            return None
        indicators["RSI"] = rsi
        ok = True
        if cond.rsi_min is not None and rsi > cond.rsi_min:
            ok = False
        if cond.rsi_max is not None and rsi < cond.rsi_max:
            ok = False
        if not ok:
            return None
        label = f"RSI {rsi}"
        if cond.rsi_min is not None:
            label += f" ≤ {cond.rsi_min}"
        if cond.rsi_max is not None:
            label += f" ≥ {cond.rsi_max}"
        matched_conditions.append(label)

    # ── 볼린저밴드 ──
    if cond.use_bollinger:
        upper, mid, lower = calc_bollinger(closes, cond.bb_period, cond.bb_k)
        if upper is None:
            return None
        indicators.update({"BB상단": upper, "BB중심": mid, "BB하단": lower})
        if cond.bb_position == "below_lower" and price > lower:
            return None
        if cond.bb_position == "above_upper" and price < upper:
            return None
        if cond.bb_position == "inside" and (price <= lower or price >= upper):
            return None
        label_map = {"below_lower": f"하단밴드({lower:,}) 이탈", "above_upper": f"상단밴드({upper:,}) 돌파", "inside": "밴드 내부"}
        matched_conditions.append(f"볼린저 {label_map[cond.bb_position]}")

    # ── MACD ──
    if cond.use_macd:
        macd_val, sig_val, hist = calc_macd(closes, cond.macd_fast, cond.macd_slow, cond.macd_signal)
        if macd_val is None:
            return None
        indicators.update({"MACD": macd_val, "Signal": sig_val, "Hist": hist})
        if cond.macd_cross == "golden" and macd_val <= sig_val:
            return None
        if cond.macd_cross == "dead" and macd_val >= sig_val:
            return None
        label = "MACD 골든크로스" if cond.macd_cross == "golden" else "MACD 데드크로스"
        matched_conditions.append(label)

    # ── 이동평균 크로스 ──
    if cond.use_ma_cross:
        cross = calc_ma_cross(closes, cond.ma_short, cond.ma_long)
        if cross is None or cross == "none":
            return None
        if cond.ma_cross == "golden" and cross != "golden":
            return None
        if cond.ma_cross == "dead" and cross != "dead":
            return None
        indicators[f"MA{cond.ma_short}"] = round(_sma(closes, cond.ma_short) or 0)
        indicators[f"MA{cond.ma_long}"]  = round(_sma(closes, cond.ma_long)  or 0)
        label = f"골든크로스(MA{cond.ma_short}/MA{cond.ma_long})" if cross == "golden" else f"데드크로스(MA{cond.ma_short}/MA{cond.ma_long})"
        matched_conditions.append(label)

    # ── 거래량 급증 ──
    if cond.use_volume:
        if len(volumes) < cond.volume_avg_days + 1:
            return None
        avg_vol = sum(volumes[-cond.volume_avg_days-1:-1]) / cond.volume_avg_days
        today_vol = volumes[-1]
        ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
        indicators["거래량비율"] = ratio
        if ratio < cond.volume_ratio:
            return None
        matched_conditions.append(f"거래량 {ratio}배 급증")

    # ── 등락률 ──
    if cond.use_change:
        indicators["등락률"] = change_pct
        ok = True
        if cond.change_min is not None and change_pct < cond.change_min:
            ok = False
        if cond.change_max is not None and change_pct > cond.change_max:
            ok = False
        if not ok:
            return None
        label = f"등락률 {change_pct:+.2f}%"
        matched_conditions.append(label)

    # ── 20일 신고가 근처 ──
    if cond.use_near_high:
        if len(ohlcv) < cond.high_days:
            return None
        high_n = max(r["high"] for r in ohlcv[-cond.high_days:])
        distance = (high_n - price) / high_n * 100 if high_n > 0 else 999
        indicators[f"{cond.high_days}일고가"] = high_n
        indicators["고가괴리%"] = round(distance, 2)
        if distance < 0:
            distance = 0
        if distance > cond.high_within_pct:
            return None
        matched_conditions.append(f"{cond.high_days}일 신고가 {distance:.2f}% 이내")

    # ── 60일선 위 ──
    if cond.use_above_ma60:
        ma60 = _sma(closes, 60)
        if ma60 is None:
            return None
        indicators["MA60"] = round(ma60)
        if price < ma60:
            return None
        matched_conditions.append(f"60일선 위 ({round(ma60):,})")

    if not matched_conditions and not (cond.use_ai or cond.use_foreign or cond.use_fundamental):
        return None

    return {
        "symbol":     symbol,
        "name":       name,
        "price":      price,
        "change_pct": change_pct,
        "volume":     current.get("volume", 0),
        "conditions": matched_conditions,
        "indicators": indicators,
    }


# ── 스크리너 실행 엔진 ────────────────────────────────────────
class Screener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self._running = False
        self._last_result: list = []
        self._progress: dict = {"total": 0, "done": 0, "status": "idle"}

    async def run(self, cond: ScreenerCondition,
                  universe: str = "all",
                  broadcast=None,
                  targets_override: Optional[dict] = None) -> list:
        """
        universe: "kospi200" | "kosdaq150" | "all"
        """
        if targets_override is not None:
            targets = targets_override
        elif universe == "kospi200":
            targets = {s[0]: s[1] for s in KOSPI200}
        elif universe == "kosdaq150":
            targets = {s[0]: s[1] for s in KOSDAQ150}
        else:
            targets = ALL_STOCKS

        total = len(targets)
        self._running = True
        self._progress = {"total": total, "done": 0, "status": "running"}
        results = []

        # 필요한 최소 봉 수
        needed = max(
            cond.rsi_period + 10 if cond.use_rsi else 0,
            cond.bb_period + 5   if cond.use_bollinger else 0,
            cond.macd_slow + 15  if cond.use_macd else 0,
            cond.ma_long + 5     if cond.use_ma_cross else 0,
            cond.volume_avg_days + 5 if cond.use_volume else 0,
            cond.high_days if cond.use_near_high else 0,
            65 if cond.use_above_ma60 else 0,
            200 if cond.use_ai else 0,
            40,
        )

        for i, (symbol, name) in enumerate(targets.items()):
            if not self._running:
                break
            try:
                ohlcv   = await self.kis.get_ohlcv(symbol, "D", needed)
                current = await self.kis.get_current_price(symbol)
                result  = screen_stock(symbol, name, ohlcv, current, cond)
                if result and cond.use_foreign:
                    investor = await self.kis.get_investor_trend(symbol, cond.foreign_days)
                    if investor["foreign_net_qty"] <= 0:
                        result = None
                    else:
                        result["conditions"].append(f"외국인 {cond.foreign_days}일 순매수 +")
                        result["indicators"]["외국인순매수"] = investor["foreign_net_qty"]
                if result and cond.use_fundamental:
                    growth = await self.kis.get_financial_growth(symbol, quarter=True)
                    growth_values = {
                        "sales": growth["sales_growth"],
                        "operating": growth["operating_profit_growth"],
                        "net": growth["net_income_growth"],
                    }
                    if cond.growth_metric == "any":
                        passed = any(v >= cond.growth_min for v in growth_values.values())
                        label_value = max(growth_values.values())
                    else:
                        label_value = growth_values.get(cond.growth_metric, 0)
                        passed = label_value >= cond.growth_min
                    if not passed:
                        result = None
                    else:
                        result["conditions"].append(f"최근 실적 성장 {label_value:+.1f}%")
                        result["indicators"].update({
                            "매출성장%": round(growth["sales_growth"], 1),
                            "영업익성장%": round(growth["operating_profit_growth"], 1),
                            "순익성장%": round(growth["net_income_growth"], 1),
                        })
                if result and cond.use_ai:
                    ai_result = self._predict_ai(symbol, ohlcv)
                    if not ai_result:
                        result = None
                    else:
                        signal = ai_result["signal"]
                        prob = {
                            "BUY": ai_result["prob_buy"],
                            "SELL": ai_result["prob_sell"],
                            "HOLD": ai_result["prob_hold"],
                        }.get(signal, 0)
                        if cond.ai_signal != "ANY" and signal != cond.ai_signal:
                            result = None
                        elif prob < cond.ai_min_prob:
                            result = None
                        else:
                            result["ai"] = ai_result
                            result["conditions"].append(f"AI {signal} {prob*100:.1f}%")
                            result["indicators"].update({
                                "AI신호": signal,
                                "AI매수%": round(ai_result["prob_buy"] * 100, 1),
                                "AI보유%": round(ai_result["prob_hold"] * 100, 1),
                                "AI매도%": round(ai_result["prob_sell"] * 100, 1),
                            })
                if result:
                    results.append(result)
                    if broadcast:
                        await broadcast({"type": "screener_hit", "data": result})
            except Exception as e:
                logger.debug(f"스크리닝 오류 {symbol}: {e}")

            self._progress["done"] = i + 1
            if broadcast:
                await broadcast({
                    "type": "screener_progress",
                    "done": i + 1,
                    "total": total,
                    "pct": round((i + 1) / total * 100),
                })
            # API 호출 제한: 초당 ~10건
            await asyncio.sleep(0.12)

        self._running = False
        self._progress["status"] = "done"
        self._last_result = results
        if broadcast:
            await broadcast({"type": "screener_done", "count": len(results)})
        return results

    def _predict_ai(self, symbol: str, ohlcv: list) -> Optional[dict]:
        optimizer = LGBMOptimizer(self.kis)
        if not optimizer.load_model(symbol):
            return None
        return optimizer.predict_latest(ohlcv)

    def stop(self):
        self._running = False

    def progress(self) -> dict:
        return self._progress

    def last_result(self) -> list:
        return self._last_result
