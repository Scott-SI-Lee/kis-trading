"""
미국 증시 마감 후 한국 증시 영향 분석기 (개선 버전)

개선사항:
- 클래스 기반 구조로 리팩토링
- 설정 관리 개선 (Config 클래스)
- 동시성 최적화 (asyncio.gather 활용)
- 에러 처리 및 로깅 강화
- 너무 긴 함수들 분리
- 타입 힌팅 완성도 향상
- 테스트 가능성 개선
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import math
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

# ============================================================================
# 설정 상수
# ============================================================================

# API 요청 설정
API_TIMEOUT_SECONDS = 15
TELEGRAM_TIMEOUT_SECONDS = 20
YAHOO_MAX_CONCURRENT_REQUESTS = 6
YAHOO_RETRY_ATTEMPTS = 2
YAHOO_RETRY_BACKOFF_SECONDS = 0.5
YAHOO_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
LOW_CONFIDENCE_MARKET_SUCCESS_RATE = 0.5

# 점수 계산 설정
SCORE_BOUNDS = (-4.0, 4.0)
YIELD_RISK_THRESHOLD_BP = 6.0
YIELD_SPIKE_THRESHOLD_BP = 12.0
RISK_PENALTY_YIELD_INCREASE = -0.5
RISK_PENALTY_DXY_INCREASE = -0.4
RISK_PENALTY_NEGATIVE_NEWS = 0.08
BOOST_POSITIVE_NEWS = 0.06

# 점수 가중치
GROWTH_BOND_WEIGHT = 0.25
FINANCIAL_BOND_WEIGHT = 0.25
ENERGY_WEIGHT = 0.8

# 방어주 부스트
HEALTHCARE_BOOST = 0.5

# 리스크 임계값
NASDAQ_CRASH_THRESHOLD = -1.0
DXY_RISK_THRESHOLD = 0.4
WTI_WEAKNESS_THRESHOLD = -1.0
MAJOR_INDEX_THRESHOLD = 0.4

# 뉴스/점수 임계값
SIGNIFICANT_MOVE_PCT = 0.5
SIGNIFICANT_MOVE_THRESHOLD = SIGNIFICANT_MOVE_PCT
SIGNIFICANT_YIELD_MOVE_BP = 5.0
NEWS_SCORE_NEWS_BOOST = 0.4
AI_CHIP_THEME = "AI/반도체"
NEWS_TITLE_WEIGHT = 2.0
NEWS_SUMMARY_WEIGHT = 1.0
NEWS_RECENCY_HALF_LIFE_HOURS = 12.0

DEFAULT_SCORING_CONFIG = {
    "score_min": SCORE_BOUNDS[0],
    "score_max": SCORE_BOUNDS[1],
    "yield_risk_threshold_bp": YIELD_RISK_THRESHOLD_BP,
    "yield_spike_threshold_bp": YIELD_SPIKE_THRESHOLD_BP,
    "risk_penalty_yield_increase": RISK_PENALTY_YIELD_INCREASE,
    "risk_penalty_dxy_increase": RISK_PENALTY_DXY_INCREASE,
    "risk_penalty_negative_news": RISK_PENALTY_NEGATIVE_NEWS,
    "boost_positive_news": BOOST_POSITIVE_NEWS,
    "growth_bond_weight": GROWTH_BOND_WEIGHT,
    "financial_bond_weight": FINANCIAL_BOND_WEIGHT,
    "energy_weight": ENERGY_WEIGHT,
    "healthcare_boost": HEALTHCARE_BOOST,
    "nasdaq_crash_threshold": NASDAQ_CRASH_THRESHOLD,
    "dxy_risk_threshold": DXY_RISK_THRESHOLD,
    "wti_weakness_threshold": WTI_WEAKNESS_THRESHOLD,
    "major_index_threshold": MAJOR_INDEX_THRESHOLD,
    "significant_move_threshold": SIGNIFICANT_MOVE_THRESHOLD,
    "significant_yield_move_bp": SIGNIFICANT_YIELD_MOVE_BP,
    "news_score_news_boost": NEWS_SCORE_NEWS_BOOST,
    "news_title_weight": NEWS_TITLE_WEIGHT,
    "news_summary_weight": NEWS_SUMMARY_WEIGHT,
    "news_recency_half_life_hours": NEWS_RECENCY_HALF_LIFE_HOURS,
}


@dataclass(frozen=True)
class Instrument:
    """거래 대상 악기 정의"""
    key: str
    label: str
    symbol: str
    group: str


@dataclass
class MarketMove:
    """시장 움직임 데이터"""
    key: str
    label: str
    symbol: str
    group: str
    price: Optional[float]
    prev_close: Optional[float]
    change_pct: Optional[float]
    fetched_at: str
    error: Optional[str] = None
    change_bp: Optional[float] = None


@dataclass
class NewsItem:
    """뉴스 아이템"""
    source: str
    title: str
    link: str
    published_at: str
    summary: str = ""


@dataclass
class Recommendation:
    """종목 추천"""
    symbol: str
    name: str
    direction: str
    score: float
    reasons: list[str]
    risks: list[str]


@dataclass
class TelegramConfig:
    """텔레그램 설정"""
    bot_token: str
    chat_id: str
    enabled: bool = True


@dataclass
class AppConfig:
    """애플리케이션 설정"""
    instruments: list[Instrument] = field(default_factory=list)
    korea_candidates: list[dict] = field(default_factory=list)
    positive_words: list[str] = field(default_factory=list)
    negative_words: list[str] = field(default_factory=list)
    news_feeds: dict[str, str] = field(default_factory=dict)
    scoring: dict[str, float] = field(default_factory=dict)
    source_path: Optional[str] = None

    @classmethod
    def load_default(cls, config_path: Optional[str] = None) -> AppConfig:
        """기본 설정 로드"""
        config = cls(
            instruments=INSTRUMENTS,
            korea_candidates=KOREA_CANDIDATES,
            positive_words=POSITIVE_WORDS,
            negative_words=NEGATIVE_WORDS,
            news_feeds=NEWS_FEEDS,
            scoring=dict(DEFAULT_SCORING_CONFIG),
        )
        path = config_path or os.getenv("US_CLOSE_CONFIG_PATH")
        if path:
            config.apply_json_file(path)
        return config

    def apply_json_file(self, path: str) -> None:
        """JSON 설정 파일로 기본 설정을 덮어쓰기"""
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if "scoring" in payload:
            self.scoring.update(payload["scoring"] or {})
        if "news_feeds" in payload:
            self.news_feeds.update(payload["news_feeds"] or {})
        if "positive_words" in payload:
            self.positive_words = list(payload["positive_words"] or [])
        if "negative_words" in payload:
            self.negative_words = list(payload["negative_words"] or [])
        if "korea_candidates" in payload:
            self.korea_candidates = list(payload["korea_candidates"] or [])
        if "instruments" in payload:
            self.instruments = [
                Instrument(**item)
                for item in payload["instruments"]
            ]
        self.source_path = path


# ============================================================================
# 악기 및 데이터 정의
# ============================================================================

INSTRUMENTS = [
    Instrument("sp500", "S&P500", "^GSPC", "major_index"),
    Instrument("nasdaq", "Nasdaq", "^IXIC", "major_index"),
    Instrument("dow", "Dow Jones", "^DJI", "major_index"),
    Instrument("russell2000", "Russell2000", "^RUT", "major_index"),
    Instrument("sox", "SOX", "^SOX", "sector"),
    Instrument("xlk", "XLK", "XLK", "sector"),
    Instrument("xle", "XLE", "XLE", "sector"),
    Instrument("xlf", "XLF", "XLF", "sector"),
    Instrument("xlv", "XLV", "XLV", "sector"),
    Instrument("nvidia", "Nvidia", "NVDA", "us_stock"),
    Instrument("amd", "AMD", "AMD", "us_stock"),
    Instrument("tsmc", "TSMC", "TSM", "us_stock"),
    Instrument("microsoft", "Microsoft", "MSFT", "us_stock"),
    Instrument("apple", "Apple", "AAPL", "us_stock"),
    Instrument("tesla", "Tesla", "TSLA", "us_stock"),
    Instrument("amazon", "Amazon", "AMZN", "us_stock"),
    Instrument("meta", "Meta", "META", "us_stock"),
    Instrument("us10y", "미국채 10년물 금리", "^TNX", "macro"),
    Instrument("dxy", "DXY", "DX-Y.NYB", "macro"),
    Instrument("wti", "WTI", "CL=F", "macro"),
    Instrument("natural_gas", "천연가스", "NG=F", "macro"),
    Instrument("gold", "금 가격", "GC=F", "macro"),
]

NEWS_FEEDS = {
    "Reuters": os.getenv("US_CLOSE_REUTERS_RSS", ""),
    "Bloomberg": os.getenv("US_CLOSE_BLOOMBERG_RSS", "https://feeds.bloomberg.com/markets/news.rss"),
    "CNBC": os.getenv("US_CLOSE_CNBC_RSS", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    "Investing": os.getenv("US_CLOSE_INVESTING_RSS", "https://www.investing.com/rss/news_25.rss"),
    "Yahoo Finance": os.getenv("US_CLOSE_YAHOO_RSS", "https://finance.yahoo.com/news/rssindex"),
}

KOREA_CANDIDATES = [
    {
        "symbol": "005930",
        "name": "삼성전자",
        "tags": ["semiconductor", "memory", "large_tech"],
        "drivers": {"sox": 0.30, "xlk": 0.15, "nvidia": 0.20, "amd": 0.10, "tsmc": 0.15, "nasdaq": 0.10},
    },
    {
        "symbol": "000660",
        "name": "SK하이닉스",
        "tags": ["semiconductor", "memory", "hbm"],
        "drivers": {"sox": 0.35, "nvidia": 0.30, "amd": 0.10, "tsmc": 0.15, "nasdaq": 0.10},
    },
    {
        "symbol": "042700",
        "name": "한미반도체",
        "tags": ["semiconductor", "hbm", "equipment"],
        "drivers": {"sox": 0.40, "nvidia": 0.45, "amd": 0.15},
    },
    {
        "symbol": "403870",
        "name": "HPSP",
        "tags": ["semiconductor", "equipment"],
        "drivers": {"sox": 0.45, "tsmc": 0.35, "amd": 0.20},
    },
    {
        "symbol": "033780",
        "name": "KT&G",
        "tags": ["defensive", "consumer"],
        "drivers": ["dow", "xlv"],
    },
    {
        "symbol": "000810",
        "name": "삼성화재",
        "tags": ["financial", "defensive"],
        "drivers": ["xlf", "us10y"],
    },
    {
        "symbol": "035420",
        "name": "NAVER",
        "tags": ["growth", "internet"],
        "drivers": {"nasdaq": 0.35, "xlk": 0.25, "meta": 0.20, "amazon": 0.20},
    },
    {
        "symbol": "035720",
        "name": "카카오",
        "tags": ["growth", "internet"],
        "drivers": {"nasdaq": 0.40, "xlk": 0.30, "meta": 0.30},
    },
    {
        "symbol": "012450",
        "name": "한화에어로스페이스",
        "tags": ["defense", "industrial"],
        "drivers": ["dow", "russell2000", "nasdaq"],
    },
    {
        "symbol": "010140",
        "name": "삼성중공업",
        "tags": ["industrial", "shipbuilding"],
        "drivers": ["dow", "wti"],
    },
    {
        "symbol": "010130",
        "name": "고려아연",
        "tags": ["materials", "industrial"],
        "drivers": ["gold", "wti"],
    },
    {
        "symbol": "373220",
        "name": "LG에너지솔루션",
        "tags": ["battery", "growth"],
        "drivers": {"tesla": 0.65, "nasdaq": 0.35},
    },
    {
        "symbol": "006400",
        "name": "삼성SDI",
        "tags": ["battery", "growth"],
        "drivers": {"tesla": 0.60, "nasdaq": 0.40},
    },
    {
        "symbol": "051910",
        "name": "LG화학",
        "tags": ["battery", "chemical"],
        "drivers": ["tesla", "wti"],
    },
    {
        "symbol": "005380",
        "name": "현대차",
        "tags": ["auto"],
        "drivers": ["dow", "russell2000", "tesla"],
    },
    {
        "symbol": "000270",
        "name": "기아",
        "tags": ["auto"],
        "drivers": ["dow", "russell2000", "tesla"],
    },
    {
        "symbol": "105560",
        "name": "KB금융",
        "tags": ["financial"],
        "drivers": ["xlf", "us10y"],
    },
    {
        "symbol": "055550",
        "name": "신한지주",
        "tags": ["financial"],
        "drivers": ["xlf", "us10y"],
    },
    {
        "symbol": "010950",
        "name": "S-Oil",
        "tags": ["energy"],
        "drivers": ["xle", "wti"],
    },
    {
        "symbol": "096770",
        "name": "SK이노베이션",
        "tags": ["energy", "battery"],
        "drivers": ["xle", "wti", "tesla"],
    },
    {
        "symbol": "006260",
        "name": "LS",
        "tags": ["industrial", "materials"],
        "drivers": ["dow", "xle", "wti"],
    },
    {
        "symbol": "207940",
        "name": "삼성바이오로직스",
        "tags": ["healthcare", "defensive"],
        "drivers": ["xlv", "dow"],
    },
    {
        "symbol": "068270",
        "name": "셀트리온",
        "tags": ["healthcare", "defensive"],
        "drivers": ["xlv", "dow"],
    },
    {
        "symbol": "145020",
        "name": "휴젤",
        "tags": ["healthcare", "growth"],
        "drivers": ["xlv", "nasdaq"],
    },
    {
        "symbol": "086790",
        "name": "하나금융지주",
        "tags": ["financial"],
        "drivers": ["xlf", "us10y"],
    },
]

POSITIVE_WORDS = [
    "rally", "surge", "gain", "record", "beat", "strong", "growth", "optimism",
    "upgrade", "ai", "chip", "semiconductor", "soft landing", "cut rates",
]

NEGATIVE_WORDS = [
    "selloff", "fall", "drop", "plunge", "miss", "weak", "recession", "tariff",
    "inflation", "higher yields", "risk", "downgrade", "war", "sanction",
]

# ============================================================================
# 유틸리티 함수
# ============================================================================


def _clean_text(value: str) -> str:
    """HTML 및 불필요한 공백 제거"""
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _fmt_pct(value: Optional[float]) -> str:
    """백분율 포매팅"""
    if value is None or not math.isfinite(value):
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_bp(value: Optional[float]) -> str:
    """bp 포매팅"""
    if value is None or not math.isfinite(value):
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}bp"


def _scoring_value(scoring: Optional[dict], key: str) -> float:
    """스코어링 설정값 조회"""
    source = scoring or DEFAULT_SCORING_CONFIG
    return float(source.get(key, DEFAULT_SCORING_CONFIG[key]))


def _score_bounds(scoring: Optional[dict]) -> tuple[float, float]:
    """스코어 상하한 조회"""
    return (
        _scoring_value(scoring, "score_min"),
        _scoring_value(scoring, "score_max"),
    )


def _score_from_pct(
    value: Optional[float],
    scale: float = 1.0,
    scoring: Optional[dict] = None,
) -> float:
    """백분율을 점수로 변환 (범위 제한)"""
    if value is None or not math.isfinite(value):
        return 0.0
    lower, upper = _score_bounds(scoring)
    return max(lower, min(upper, value * scale))


def _score_from_bp(
    value: Optional[float],
    scale: float = 1.0,
    scoring: Optional[dict] = None,
) -> float:
    """bp 변화를 점수로 변환. 10bp를 1점 단위로 정규화합니다."""
    if value is None or not math.isfinite(value):
        return 0.0
    lower, upper = _score_bounds(scoring)
    return max(lower, min(upper, (value / 10.0) * scale))


def _get_move_safe(moves: dict[str, MarketMove], key: str) -> Optional[MarketMove]:
    """안전한 MarketMove 조회"""
    return moves.get(key)


def _get_pct_safe(moves: dict[str, MarketMove], key: str) -> Optional[float]:
    """안전한 change_pct 조회"""
    move = moves.get(key)
    return move.change_pct if move else None


def _get_bp_safe(moves: dict[str, MarketMove], key: str) -> Optional[float]:
    """안전한 change_bp 조회"""
    move = moves.get(key)
    return move.change_bp if move else None


def _iter_weighted_drivers(drivers: object) -> list[tuple[str, float]]:
    """list/dict 드라이버 설정을 가중치 목록으로 정규화"""
    if isinstance(drivers, dict):
        weighted = [(str(key), float(weight)) for key, weight in drivers.items()]
    else:
        keys = [str(key) for key in (drivers or [])]
        weighted = [(key, 1.0) for key in keys]

    total = sum(weight for _, weight in weighted if weight > 0)
    if total <= 0:
        return []
    return [(key, weight / total) for key, weight in weighted if weight > 0]


def _basis_point_change(price: Optional[float], prev_close: Optional[float]) -> Optional[float]:
    """금리 레벨 변화량을 bp로 변환"""
    if price is None or prev_close is None:
        return None
    return round((float(price) - float(prev_close)) * 100, 1)


# ============================================================================
# 클라이언트 클래스
# ============================================================================


class MarketDataFetchError(Exception):
    """시장 데이터 수집 에러"""
    pass


class TransientMarketDataFetchError(MarketDataFetchError):
    """재시도 가능한 시장 데이터 수집 에러"""
    pass


class YahooMarketDataClient:
    """Yahoo Finance 마켓 데이터 클라이언트"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        max_concurrent_requests: int = YAHOO_MAX_CONCURRENT_REQUESTS,
        retry_attempts: int = YAHOO_RETRY_ATTEMPTS,
        retry_backoff_seconds: float = YAHOO_RETRY_BACKOFF_SECONDS,
    ):
        self.session = session
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent_requests))
        self._retry_attempts = max(0, retry_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)

    async def fetch_moves(self, instruments: list[Instrument]) -> list[MarketMove]:
        """여러 악기의 마켓 데이터 동시 수집"""
        tasks = [self.fetch_move(instr) for instr in instruments]
        # 속도 제한: 동시 요청 수 제어
        return await asyncio.gather(*tasks)

    async def fetch_move(self, instrument: Instrument) -> MarketMove:
        """단일 악기의 마켓 데이터 수집"""
        async with self._semaphore:
            return await self._fetch_move_with_retries(instrument)

    async def _fetch_move_with_retries(self, instrument: Instrument) -> MarketMove:
        """재시도 가능한 실패에 대해 지수 백오프로 마켓 데이터 수집"""
        for attempt in range(self._retry_attempts + 1):
            try:
                return await self._fetch_move_once(instrument)
            except asyncio.TimeoutError:
                message = "timeout"
                retryable = True
            except TransientMarketDataFetchError as exc:
                message = str(exc)
                retryable = True
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", instrument.symbol, type(exc).__name__)
                return self._error(instrument, str(exc))

            if not retryable or attempt >= self._retry_attempts:
                logger.warning("Yahoo fetch failed for %s after retries: %s", instrument.symbol, message)
                return self._error(instrument, message)

            delay = self._retry_backoff_seconds * (2 ** attempt)
            logger.warning(
                "Transient Yahoo fetch error for %s (%s); retrying in %.1fs",
                instrument.symbol,
                message,
                delay,
            )
            if delay > 0:
                await asyncio.sleep(delay)

        return self._error(instrument, "retry exhausted")

    async def _fetch_move_once(self, instrument: Instrument) -> MarketMove:
        """단일 Yahoo API 요청을 실행하고 응답을 MarketMove로 변환"""
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(instrument.symbol, safe='')}?range=5d&interval=1d"
        )
        async with self.session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS),
            headers={
                "User-Agent": "Mozilla/5.0 kis-trading/1.0",
                "Accept": "application/json,text/plain,*/*",
            },
        ) as resp:
            if resp.status in YAHOO_TRANSIENT_HTTP_STATUSES:
                logger.warning("Yahoo API transient HTTP %d for %s", resp.status, instrument.symbol)
                raise TransientMarketDataFetchError(f"HTTP {resp.status}")
            if resp.status >= 400:
                logger.warning("Yahoo API HTTP %d for %s", resp.status, instrument.symbol)
                return self._error(instrument, f"HTTP {resp.status}")
            payload = await resp.json(content_type=None)

        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            logger.warning("Empty chart result for %s", instrument.symbol)
            return self._error(instrument, "empty chart result")

        meta = result.get("meta", {})
        quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = [v for v in quote_data.get("close", []) if v is not None]

        price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
        prev_close = meta.get("chartPreviousClose")
        if prev_close is None and len(closes) >= 2:
            prev_close = closes[-2]

        change_pct = None
        if price is not None and prev_close:
            change_pct = round((float(price) / float(prev_close) - 1) * 100, 3)
        change_bp = (
            _basis_point_change(price, prev_close)
            if instrument.key == "us10y"
            else None
        )

        return MarketMove(
            key=instrument.key,
            label=instrument.label,
            symbol=instrument.symbol,
            group=instrument.group,
            price=round(float(price), 4) if price is not None else None,
            prev_close=round(float(prev_close), 4) if prev_close is not None else None,
            change_pct=change_pct,
            fetched_at=datetime.now(UTC).isoformat(),
            change_bp=change_bp,
        )

    @staticmethod
    def _error(instrument: Instrument, message: str) -> MarketMove:
        """에러 MarketMove 생성"""
        return MarketMove(
            key=instrument.key,
            label=instrument.label,
            symbol=instrument.symbol,
            group=instrument.group,
            price=None,
            prev_close=None,
            change_pct=None,
            fetched_at=datetime.now(UTC).isoformat(),
            error=message,
        )


class RssNewsClient:
    """RSS 뉴스 클라이언트"""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch_recent(
        self,
        feeds: dict[str, str],
        hours: int = 24,
        per_source: int = 10,
    ) -> list[NewsItem]:
        """최근 뉴스 수집"""
        tasks = [
            self._fetch_feed(source, url, hours, per_source)
            for source, url in feeds.items()
            if url  # 빈 URL 스킵
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        items = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("News fetch error: %s", result)
                continue
            items.extend(result)

        items.sort(key=lambda item: item.published_at, reverse=True)
        return items

    async def _fetch_feed(
        self,
        source: str,
        url: str,
        hours: int,
        limit: int,
    ) -> list[NewsItem]:
        """단일 뉴스 피드 수집"""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS),
                headers={"User-Agent": "kis-trading/1.0"},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("RSS fetch HTTP %d for %s", resp.status, source)
                    return []
                text = await resp.text()
            root = ElementTree.fromstring(text)
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching RSS from %s", source)
            return []
        except Exception as exc:
            logger.debug("Failed to parse RSS from %s: %s", source, type(exc).__name__)
            return []

        items = []
        for node in root.findall(".//item"):
            title = _clean_text(node.findtext("title") or "")
            link = _clean_text(node.findtext("link") or "")
            summary = _clean_text(node.findtext("description") or "")
            published = self._parse_date(
                node.findtext("pubDate")
                or node.findtext("{http://purl.org/dc/elements/1.1/}date")
            )

            if not title or published is None or published < cutoff:
                continue

            items.append(
                NewsItem(
                    source=source,
                    title=title,
                    link=link,
                    published_at=published.astimezone(UTC).isoformat(),
                    summary=summary,
                )
            )
            if len(items) >= limit:
                break

        return items

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[datetime]:
        """뉴스 발행일 파싱"""
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except Exception:
            return None


# ============================================================================
# 분석 함수
# ============================================================================


def _keyword_hits(text: str, keyword: str) -> int:
    """키워드가 독립 표현으로 등장한 횟수 계산"""
    pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
    return len(re.findall(pattern, text.lower()))


def _news_recency_weight(
    published_at: str,
    now: datetime,
    scoring: Optional[dict] = None,
) -> float:
    """최근 기사에 더 높은 가중치 부여"""
    try:
        published = datetime.fromisoformat(published_at).astimezone(UTC)
    except Exception:
        return 1.0
    age_hours = max(0.0, (now - published).total_seconds() / 3600)
    half_life = max(0.1, _scoring_value(scoring, "news_recency_half_life_hours"))
    return 0.5 ** (age_hours / half_life)


def _dedupe_news(news: list[NewsItem]) -> list[NewsItem]:
    """링크/제목 기반 뉴스 중복 제거"""
    seen = set()
    deduped = []
    for item in news:
        key = (item.link or item.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _news_signal(
    news: list[NewsItem],
    scoring: Optional[dict] = None,
    positive_words: Optional[list[str]] = None,
    negative_words: Optional[list[str]] = None,
) -> dict:
    """뉴스에서 신호 추출"""
    now = datetime.now(UTC)
    deduped_news = _dedupe_news(news)
    positive = 0.0
    negative = 0.0
    theme_counts: dict[str, float] = {}

    theme_keywords = {
        AI_CHIP_THEME: ["ai", "chip", "semiconductor", "nvidia", "tsmc", "memory"],
        "금리/달러": ["yield", "fed", "rate", "dollar", "inflation"],
        "에너지": ["oil", "wti", "energy", "gas"],
        "전기차/배터리": ["tesla", "ev", "battery"],
        "지정학/관세": ["tariff", "war", "sanction", "china"],
    }

    for item in deduped_news:
        recency_weight = _news_recency_weight(item.published_at, now, scoring)
        title = item.title.lower()
        summary = item.summary.lower()
        title_weight = _scoring_value(scoring, "news_title_weight")
        summary_weight = _scoring_value(scoring, "news_summary_weight")

        positive += recency_weight * sum(
            title_weight * _keyword_hits(title, word)
            + summary_weight * _keyword_hits(summary, word)
            for word in (positive_words or POSITIVE_WORDS)
        )
        negative += recency_weight * sum(
            title_weight * _keyword_hits(title, word)
            + summary_weight * _keyword_hits(summary, word)
            for word in (negative_words or NEGATIVE_WORDS)
        )

        for label, words in theme_keywords.items():
            count = sum(
                title_weight * _keyword_hits(title, word)
                + summary_weight * _keyword_hits(summary, word)
                for word in words
            )
            if count:
                theme_counts[label] = theme_counts.get(label, 0.0) + recency_weight * count

    themes = [
        {"theme": label, "count": round(count, 2)}
        for label, count in theme_counts.items()
    ]

    themes.sort(key=lambda item: item["count"], reverse=True)
    positive_hits = round(positive, 2)
    negative_hits = round(negative, 2)

    return {
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
        "net_score": round(positive_hits - negative_hits, 2),
        "themes": themes[:5],
        "deduped_news_count": len(deduped_news),
    }


def _calculate_risk_penalty(
    moves: dict[str, MarketMove],
    news_signal: dict,
    scoring: Optional[dict] = None,
) -> float:
    """리스크 페널티 계산 (개선: 함수 분리)"""
    penalty = 0.0

    # 금리 상승: 금리는 % 변화율보다 bp 변화가 리스크 판단에 적합합니다.
    yield_bp = _get_bp_safe(moves, "us10y") or 0
    if yield_bp > _scoring_value(scoring, "yield_risk_threshold_bp"):
        penalty += _scoring_value(scoring, "risk_penalty_yield_increase")

    # 달러 강세
    dxy_pct = _get_pct_safe(moves, "dxy") or 0
    if dxy_pct > _scoring_value(scoring, "dxy_risk_threshold"):
        penalty += _scoring_value(scoring, "risk_penalty_dxy_increase")

    # 뉴스 신호
    net_score = news_signal["net_score"]
    if net_score < 0:
        penalty -= min(0.8, abs(net_score) * _scoring_value(scoring, "risk_penalty_negative_news"))
    elif net_score > 0:
        penalty += min(0.6, net_score * _scoring_value(scoring, "boost_positive_news"))

    return penalty


def _apply_sector_adjustments(
    score: float,
    candidate: dict,
    moves: dict[str, MarketMove],
    news_signal: dict,
    reasons: list[str],
    risks: list[str],
    scoring: Optional[dict] = None,
) -> float:
    """섹터별 점수 조정 (개선: 함수 분리)"""
    tags = set(candidate["tags"])

    # 반도체
    if "semiconductor" in tags:
        if any(t["theme"] == AI_CHIP_THEME for t in news_signal["themes"]):
            score += _scoring_value(scoring, "news_score_news_boost")
            reasons.append("AI/반도체 뉴스 빈도 증가")

    # 성장주
    if "growth" in tags:
        yield_bp = _get_bp_safe(moves, "us10y")
        score -= _score_from_bp(yield_bp, _scoring_value(scoring, "growth_bond_weight"), scoring)
        dxy_pct = _get_pct_safe(moves, "dxy") or 0
        if dxy_pct > _scoring_value(scoring, "dxy_risk_threshold"):
            risks.append("달러 강세는 외국인 수급 부담")

    # 금융
    if "financial" in tags:
        yield_bp = _get_bp_safe(moves, "us10y")
        score += _score_from_bp(yield_bp, _scoring_value(scoring, "financial_bond_weight"), scoring)
        if (yield_bp or 0) < -_scoring_value(scoring, "yield_risk_threshold_bp"):
            risks.append("금리 급락 시 은행 순이자마진 기대 약화")

    # 에너지
    if "energy" in tags:
        wti_pct = _get_pct_safe(moves, "wti")
        score += _score_from_pct(wti_pct, _scoring_value(scoring, "energy_weight"), scoring)
        if (wti_pct or 0) < _scoring_value(scoring, "wti_weakness_threshold"):
            risks.append("유가 약세는 정유/에너지 투자심리 부담")

    # 방어주
    if "healthcare" in tags and score < 0:
        score += _scoring_value(scoring, "healthcare_boost")
        reasons.append("방어주 성격으로 하락장 상대 강도 기대")

    return score


def _add_universal_risks(
    risks: list[str],
    moves: dict[str, MarketMove],
    candidate: dict,
    scoring: Optional[dict] = None,
) -> None:
    """공통 리스크 추가 (개선: 함수 분리)"""
    tags = set(candidate["tags"])

    nasdaq_pct = _get_pct_safe(moves, "nasdaq") or 0
    if (
        nasdaq_pct < _scoring_value(scoring, "nasdaq_crash_threshold")
        and "growth" in tags
    ):
        risks.append("나스닥 약세 시 성장주 밸류에이션 부담")

    yield_bp = _get_bp_safe(moves, "us10y") or 0
    if yield_bp > _scoring_value(scoring, "yield_spike_threshold_bp"):
        risks.append(f"미 10년물 금리 급등({_fmt_bp(yield_bp)})")


def _is_significant_driver_move(
    key: str,
    move: MarketMove,
    scoring: Optional[dict] = None,
) -> bool:
    """드라이버 변동이 추천 사유로 표시할 만큼 의미 있는지 판정"""
    if key == "us10y":
        return abs(move.change_bp or 0.0) >= _scoring_value(
            scoring,
            "significant_yield_move_bp",
        )
    return abs(move.change_pct or 0.0) >= _scoring_value(
        scoring,
        "significant_move_threshold",
    )


def _build_recommendations(
    moves: dict[str, MarketMove],
    news_signal: dict,
    candidates: list[dict],
    scoring: Optional[dict] = None,
) -> list[Recommendation]:
    """종목 추천 생성 (개선: 섹터 조정 함수화)"""
    recommendations = []
    risk_penalty = _calculate_risk_penalty(moves, news_signal, scoring)

    for candidate in candidates:
        driver_scores = []
        reasons = []
        risks = []

        # 드라이버 점수 계산
        for key, weight in _iter_weighted_drivers(candidate.get("drivers")):
            move = moves.get(key)
            if not move or move.change_pct is None:
                continue
            pct = move.change_pct
            score_unit = (
                _score_from_bp(move.change_bp, scoring=scoring)
                if key == "us10y"
                else _score_from_pct(pct, scoring=scoring)
            )
            driver_scores.append(score_unit * weight)
            if _is_significant_driver_move(key, move, scoring):
                detail = _fmt_bp(move.change_bp) if key == "us10y" else _fmt_pct(pct)
                reasons.append(f"{move.label} {detail}")

        # 기본 점수
        score = (sum(driver_scores) if driver_scores else 0.0) + risk_penalty

        # 섹터별 조정
        score = _apply_sector_adjustments(
            score, candidate, moves, news_signal, reasons, risks, scoring
        )

        # 기본 이유 추가
        if not reasons:
            reasons.append("미국 마감 데이터와 매크로 신호 종합")

        # 공통 리스크 추가
        _add_universal_risks(risks, moves, candidate, scoring)

        recommendations.append(
            Recommendation(
                symbol=candidate["symbol"],
                name=candidate["name"],
                direction="up" if score >= 0 else "down",
                score=round(score, 2),
                reasons=reasons[:4],
                risks=risks[:3],
            )
        )

    recommendations.sort(key=lambda item: item.score, reverse=True)
    return recommendations


def _market_summary(
    moves: dict[str, MarketMove],
    scoring: Optional[dict] = None,
) -> list[str]:
    """시장 요약 생성"""
    bullets = []
    major_keys = ["sp500", "nasdaq", "dow", "russell2000"]
    sector_keys = ["sox", "xlk", "xle", "xlf", "xlv"]

    # 메이저 지수 평균
    major_avg = sum(
        _score_from_pct(_get_pct_safe(moves, k), scoring=scoring) for k in major_keys
    ) / len(major_keys)

    # 섹터 리더/래거
    sector_moves = [
        moves[k] for k in sector_keys
        if k in moves and _get_pct_safe(moves, k) is not None
    ]
    sector_leader = max(sector_moves, key=lambda x: x.change_pct, default=None)
    sector_lagger = min(sector_moves, key=lambda x: x.change_pct, default=None)

    # 요약 텍스트
    major_index_threshold = _scoring_value(scoring, "major_index_threshold")
    if major_avg > major_index_threshold:
        bullets.append("미국 주요 지수는 위험선호 우위로 마감했습니다.")
    elif major_avg < -major_index_threshold:
        bullets.append("미국 주요 지수는 위험회피 우위로 마감했습니다.")
    else:
        bullets.append("미국 주요 지수는 혼조권으로 마감했습니다.")

    if sector_leader:
        bullets.append(
            f"섹터 강세는 {sector_leader.label}({_fmt_pct(sector_leader.change_pct)})가 주도했습니다."
        )
    if sector_lagger:
        bullets.append(
            f"섹터 약세는 {sector_lagger.label}({_fmt_pct(sector_lagger.change_pct)})가 두드러졌습니다."
        )

    yield_move = _get_move_safe(moves, "us10y")
    if yield_move and yield_move.change_pct is not None:
        bullets.append(f"미 10년물 금리는 {_fmt_bp(yield_move.change_bp)} 움직였습니다.")

    dxy_move = _get_move_safe(moves, "dxy")
    if dxy_move and dxy_move.change_pct is not None:
        bullets.append(
            f"DXY는 {_fmt_pct(dxy_move.change_pct)}로 외국인 수급에 영향을 줄 수 있습니다."
        )

    return bullets


def _data_quality(
    market_results: list[MarketMove],
    news: list[NewsItem],
    feeds: dict[str, str],
) -> dict:
    """보고서 신뢰도 점검용 메타데이터 생성"""
    total = len(market_results)
    failed = [item for item in market_results if item.error or item.change_pct is None]
    news_by_source: dict[str, int] = {}
    for item in news:
        news_by_source[item.source] = news_by_source.get(item.source, 0) + 1

    active_sources = [source for source, url in feeds.items() if url]
    market_success_count = total - len(failed)
    market_success_rate = round(market_success_count / total, 3) if total else 0
    low_confidence = market_success_rate < LOW_CONFIDENCE_MARKET_SUCCESS_RATE
    return {
        "market_success_count": market_success_count,
        "market_total_count": total,
        "market_success_rate": market_success_rate,
        "low_confidence": low_confidence,
        "low_confidence_reason": (
            "market_success_rate_below_threshold"
            if low_confidence
            else None
        ),
        "market_success_rate_threshold": LOW_CONFIDENCE_MARKET_SUCCESS_RATE,
        "failed_market_symbols": [
            {"symbol": item.symbol, "label": item.label, "error": item.error or "missing change_pct"}
            for item in failed
        ],
        "news_count": len(news),
        "news_source_count": len(news_by_source),
        "active_news_source_count": len(active_sources),
        "news_by_source": news_by_source,
    }


# ============================================================================
# 포매팅 함수
# ============================================================================


def _compose_markdown(report: dict) -> str:
    """마크다운 보고서 작성"""
    lines = [
        "# 미국 마감 후 한국 증시 영향 브리프",
        "",
        f"- 생성시각: {report['generated_at_kst']}",
        f"- 뉴스 기준: 최근 {report['news_window_hours']}시간",
    ]
    if report.get("low_confidence"):
        detail = report.get("low_confidence_detail") or "시장 데이터 수집 성공률이 낮습니다."
        lines.extend([
            "",
            f"> ⚠️ 데이터 신뢰도 경고: {detail} 추천 후보는 표시하지 않습니다.",
        ])

    lines.extend(["", "## 핵심 요약"])
    lines.extend(f"- {item}" for item in report["summary"])

    quality = report.get("data_quality") or {}
    if quality:
        lines.extend([
            "",
            "## 데이터 품질",
            (
                f"- 시장 데이터: {quality.get('market_success_count', 0)}/"
                f"{quality.get('market_total_count', 0)} 성공"
                f" (성공률 {quality.get('market_success_rate', 0):.1%})"
            ),
            (
                f"- 뉴스: {quality.get('news_count', 0)}건 / "
                f"{quality.get('news_source_count', 0)}개 소스"
            ),
        ])
        failed = quality.get("failed_market_symbols") or []
        if failed:
            failed_text = ", ".join(f"{item['label']}({item['error']})" for item in failed[:5])
            lines.append(f"- 수집 실패: {failed_text}")

    lines.extend(["", "## 상승 가능 후보"])
    if report.get("low_confidence"):
        lines.append("- 데이터 신뢰도 부족으로 추천을 생성하지 않았습니다.")
    else:
        for item in report["recommendations"]["up"]:
            reason = "; ".join(item["reasons"])
            risk = f" / 리스크: {'; '.join(item['risks'])}" if item["risks"] else ""
            lines.append(f"- {item['name']}({item['symbol']}) score {item['score']}: {reason}{risk}")

    lines.extend(["", "## 하락 위험 후보"])
    if report.get("low_confidence"):
        lines.append("- 데이터 신뢰도 부족으로 추천을 생성하지 않았습니다.")
    else:
        for item in report["recommendations"]["down"]:
            reason = "; ".join(item["reasons"])
            risk = f" / 체크: {'; '.join(item['risks'])}" if item["risks"] else ""
            lines.append(f"- {item['name']}({item['symbol']}) score {item['score']}: {reason}{risk}")

    lines.extend(["", "## 주요 데이터"])
    for move in report["market_data"]:
        detail = _fmt_bp(move.get("change_bp")) if move["key"] == "us10y" else _fmt_pct(move["change_pct"])
        lines.append(f"- {move['label']}: {detail}")

    lines.extend(["", "## 최근 뉴스"])
    for item in report["news"][:10]:
        lines.append(f"- [{item['source']}] {item['title']} ({item['published_at']})")

    return "\n".join(lines)


def _compose_telegram_message(report: dict) -> str:
    """텔레그램 메시지 작성"""
    lines = [
        "미국 마감 후 한국 증시 브리프",
        f"생성시각: {report['generated_at_kst']}",
    ]

    summary = report.get("summary") or []
    if summary:
        lines.append("")
        lines.append("핵심 요약")
        lines.extend(f"- {item}" for item in summary[:5])

    quality = report.get("data_quality") or {}
    if quality:
        lines.append("")
        lines.append(
            "데이터 "
            f"{quality.get('market_success_count', 0)}/{quality.get('market_total_count', 0)} 성공, "
            f"뉴스 {quality.get('news_count', 0)}건"
        )

    if report.get("low_confidence"):
        lines.append("")
        lines.append(report.get("low_confidence_detail") or "데이터 신뢰도 부족")

    rec_up = (report.get("recommendations") or {}).get("up") or []
    rec_down = (report.get("recommendations") or {}).get("down") or []

    if rec_up:
        lines.append("")
        lines.append("상승 가능 후보")
        for item in rec_up[:5]:
            lines.append(f"- {item['name']}({item['symbol']}) {item['score']:+.2f}")

    if rec_down:
        lines.append("")
        lines.append("하락 위험 후보")
        for item in rec_down[:5]:
            lines.append(f"- {item['name']}({item['symbol']}) {item['score']:+.2f}")

    return "\n".join(lines)


# ============================================================================
# 텔레그램 함수
# ============================================================================


def get_telegram_config() -> Optional[TelegramConfig]:
    """텔레그램 설정 로드"""
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("US_CLOSE_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("US_CLOSE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    enabled_value = os.getenv("US_CLOSE_TELEGRAM_ENABLED", "true").lower()
    enabled = enabled_value not in ("0", "false", "no", "off")
    return TelegramConfig(bot_token=token, chat_id=chat_id, enabled=enabled)


async def send_telegram_message(text: str, config: Optional[TelegramConfig] = None) -> dict:
    """텔레그램 메시지 전송"""
    config = config or get_telegram_config()
    if config is None or not config.enabled:
        return {"ok": False, "detail": "텔레그램 설정이 비활성화되어 있습니다"}

    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = {
        "chat_id": config.chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=TELEGRAM_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("telegram send failed: HTTP %s", resp.status)
                    return {"ok": False, "detail": body}
                return {"ok": True, "detail": "sent"}
    except asyncio.TimeoutError:
        logger.warning("telegram send timeout")
        return {"ok": False, "detail": "timeout"}
    except Exception as exc:
        logger.warning("telegram send failed: %s", type(exc).__name__)
        return {"ok": False, "detail": str(exc)}


async def send_report_telegram(report: dict) -> dict:
    """보고서 신뢰도를 확인한 뒤 텔레그램 전송"""
    if report.get("low_confidence"):
        detail = report.get("low_confidence_detail") or "low_confidence"
        logger.warning("telegram send skipped: %s", detail)
        return {"ok": False, "detail": f"skipped: {detail}"}
    return await send_telegram_message(_compose_telegram_message(report))


# ============================================================================
# 파일 저장 함수
# ============================================================================


def _default_output_dir() -> str:
    """기본 출력 디렉토리"""
    base_dir = os.path.dirname(__file__)
    return os.getenv("US_CLOSE_OUTPUT_DIR", os.path.join(base_dir, "reports"))


def _ensure_output_dir(path: str) -> None:
    """출력 디렉토리 생성"""
    os.makedirs(path, exist_ok=True)


def _atomic_write_text(path: str, content: str) -> None:
    """임시 파일에 쓴 뒤 교체하여 latest 파일 손상을 방지"""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def _atomic_write_json(path: str, payload: dict) -> None:
    """JSON 파일 원자적 저장"""
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


async def save_us_close_report(report: dict, output_dir: Optional[str] = None) -> dict:
    """보고서 저장"""
    output_dir = output_dir or _default_output_dir()
    _ensure_output_dir(output_dir)

    timestamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    latest_json = os.path.join(output_dir, "latest.json")
    latest_md = os.path.join(output_dir, "latest.md")
    history_json = os.path.join(output_dir, f"us-close-{timestamp}.json")
    history_md = os.path.join(output_dir, f"us-close-{timestamp}.md")

    report["saved_to"] = {
        "latest_json": latest_json,
        "latest_md": latest_md,
        "history_json": history_json,
        "history_md": history_md,
    }

    _atomic_write_json(latest_json, report)
    _atomic_write_text(latest_md, report["markdown"])
    _atomic_write_json(history_json, report)
    _atomic_write_text(history_md, report["markdown"])

    logger.info("Report saved: %s", latest_json)
    return report


# ============================================================================
# 추천 피드백 함수
# ============================================================================


def _load_json_file(path: str) -> dict:
    """JSON 파일 로드"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_realized_return_pct(value: object) -> Optional[float]:
    """실현 수익률 입력에서 pct 값을 추출"""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, dict):
        return None

    for key in ("return_pct", "change_pct", "pct", "return"):
        if key in value and value[key] is not None:
            return float(value[key])

    close = value.get("close")
    prev_close = (
        value.get("prev_close")
        or value.get("previous_close")
        or value.get("entry_close")
    )
    if close is None or not prev_close:
        return None
    return round((float(close) / float(prev_close) - 1) * 100, 3)


def _normalize_realized_returns(payload: dict) -> dict[str, float]:
    """실현 수익률 JSON을 symbol -> return_pct 형태로 정규화"""
    source = payload.get("returns") or payload.get("realized_returns") or payload
    returns: dict[str, float] = {}
    for symbol, value in source.items():
        return_pct = _extract_realized_return_pct(value)
        if return_pct is not None and math.isfinite(return_pct):
            returns[str(symbol)] = round(return_pct, 3)
    return returns


def evaluate_recommendation_feedback(
    report: dict,
    realized_returns: dict[str, float],
) -> dict:
    """추천 방향과 다음 거래일 실현 수익률을 비교해 적중률 계산"""
    recommendations = report.get("recommendations") or {}
    entries = []
    for bucket, expected_direction in (("up", "up"), ("down", "down")):
        for item in recommendations.get(bucket) or []:
            symbol = str(item.get("symbol", ""))
            if not symbol or symbol not in realized_returns:
                continue
            return_pct = realized_returns[symbol]
            hit = return_pct > 0 if expected_direction == "up" else return_pct < 0
            entries.append({
                "symbol": symbol,
                "name": item.get("name"),
                "direction": expected_direction,
                "score": item.get("score"),
                "return_pct": return_pct,
                "hit": hit,
            })

    missed_symbols = [
        str(item.get("symbol", ""))
        for bucket in ("up", "down")
        for item in recommendations.get(bucket) or []
        if str(item.get("symbol", "")) not in realized_returns
    ]
    hit_count = sum(1 for item in entries if item["hit"])
    total = len(entries)
    by_direction = {}
    for direction in ("up", "down"):
        subset = [item for item in entries if item["direction"] == direction]
        direction_hits = sum(1 for item in subset if item["hit"])
        by_direction[direction] = {
            "evaluated_count": len(subset),
            "hit_count": direction_hits,
            "hit_rate": round(direction_hits / len(subset), 3) if subset else None,
        }

    return {
        "evaluated_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "report_generated_at_kst": report.get("generated_at_kst"),
        "low_confidence": bool(report.get("low_confidence")),
        "evaluated_count": total,
        "hit_count": hit_count,
        "hit_rate": round(hit_count / total, 3) if total else None,
        "by_direction": by_direction,
        "missed_symbols": [symbol for symbol in missed_symbols if symbol],
        "entries": entries,
    }


def append_feedback_log(feedback: dict, output_path: Optional[str] = None) -> str:
    """피드백 결과를 JSONL로 누적 저장"""
    path = output_path or os.path.join(_default_output_dir(), "us-close-feedback.jsonl")
    directory = os.path.dirname(path)
    if directory:
        _ensure_output_dir(directory)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(feedback, ensure_ascii=False) + "\n")
    return path


def run_recommendation_feedback(
    report_path: str,
    realized_returns_path: str,
    output_path: Optional[str] = None,
) -> dict:
    """저장된 보고서와 실현 수익률 파일로 적중률 피드백 실행"""
    report = _load_json_file(report_path)
    realized_returns = _normalize_realized_returns(_load_json_file(realized_returns_path))
    feedback = evaluate_recommendation_feedback(report, realized_returns)
    feedback["feedback_log"] = append_feedback_log(feedback, output_path)
    return feedback


# ============================================================================
# 메인 진입점
# ============================================================================


async def run_us_close_job(
    news_window_hours: int = 24,
    news_per_source: int = 10,
    output_dir: Optional[str] = None,
    telegram: bool = False,
    config: Optional[AppConfig] = None,
) -> dict:
    """US Close 분석 작업 실행"""
    config = config or AppConfig.load_default()
    report = await build_us_close_report(
        news_window_hours=news_window_hours,
        news_per_source=news_per_source,
        config=config,
    )
    report = await save_us_close_report(report, output_dir=output_dir)
    if telegram:
        result = await send_report_telegram(report)
        report["telegram_sent"] = result.get("ok", False)
        report["telegram_detail"] = result.get("detail")
    return report


async def send_latest_us_close_telegram(
    news_window_hours: int = 24,
    news_per_source: int = 10,
    config: Optional[AppConfig] = None,
) -> dict:
    """최신 US Close 보고서를 텔레그램으로 전송"""
    config = config or AppConfig.load_default()
    report = await build_us_close_report(
        news_window_hours=news_window_hours,
        news_per_source=news_per_source,
        config=config,
    )
    result = await send_report_telegram(report)
    report["telegram_sent"] = result.get("ok", False)
    report["telegram_detail"] = result.get("detail")
    return report


async def schedule_us_close_job(
    hour_kst: int = 6,
    minute_kst: int = 0,
    news_window_hours: int = 24,
    news_per_source: int = 10,
    output_dir: Optional[str] = None,
    telegram: bool = False,
    config: Optional[AppConfig] = None,
) -> None:
    """US Close 분석 정기 실행 스케줄"""
    if not (0 <= hour_kst <= 23):
        raise ValueError("hour_kst must be 0..23")
    if not (0 <= minute_kst <= 59):
        raise ValueError("minute_kst must be 0..59")

    config = config or AppConfig.load_default()

    while True:
        now = datetime.now(KST)
        next_run = now.replace(hour=hour_kst, minute=minute_kst, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            "Next run scheduled for %s KST (in %d seconds)",
            next_run.isoformat(timespec="seconds"),
            int(sleep_seconds),
        )
        await asyncio.sleep(sleep_seconds)

        try:
            logger.info("Starting scheduled US close job")
            report = await run_us_close_job(
                news_window_hours=news_window_hours,
                news_per_source=news_per_source,
                output_dir=output_dir,
                telegram=telegram,
                config=config,
            )
            logger.info("Scheduled job completed")
        except Exception as exc:
            logger.exception("Scheduled job failed")


async def build_us_close_report(
    news_window_hours: int = 24,
    news_per_source: int = 10,
    config: Optional[AppConfig] = None,
) -> dict:
    """US Close 분석 보고서 생성"""
    config = config or AppConfig.load_default()

    async with aiohttp.ClientSession() as session:
        market_client = YahooMarketDataClient(session)
        news_client = RssNewsClient(session)

        # 뉴스와 마켓 데이터 동시 수집
        news_task = asyncio.create_task(
            news_client.fetch_recent(
                feeds=config.news_feeds,
                hours=news_window_hours,
                per_source=news_per_source,
            )
        )
        market_results = await market_client.fetch_moves(config.instruments)
        news = await news_task

    moves = {item.key: item for item in market_results}
    signal = _news_signal(
        news,
        scoring=config.scoring,
        positive_words=config.positive_words,
        negative_words=config.negative_words,
    )
    data_quality = _data_quality(market_results, news, config.news_feeds)
    low_confidence = bool(data_quality.get("low_confidence"))
    low_confidence_detail = ""
    if low_confidence:
        low_confidence_detail = (
            "시장 데이터 성공률 "
            f"{data_quality.get('market_success_rate', 0):.1%}가 "
            f"임계값 {data_quality.get('market_success_rate_threshold', 0):.0%} 미만입니다."
        )

    recs = (
        []
        if low_confidence
        else _build_recommendations(moves, signal, config.korea_candidates, config.scoring)
    )

    up = [asdict(item) for item in recs if item.score > 0][:8]
    down = [asdict(item) for item in sorted(recs, key=lambda item: item.score) if item.score < 0][:8]

    report = {
        "generated_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "news_window_hours": news_window_hours,
        "low_confidence": low_confidence,
        "low_confidence_detail": low_confidence_detail or None,
        "config_source": config.source_path,
        "scoring_config": config.scoring,
        "summary": _market_summary(moves, config.scoring),
        "news_signal": signal,
        "data_quality": data_quality,
        "market_data": [asdict(item) for item in market_results],
        "news": [asdict(item) for item in news],
        "recommendations": {"up": up, "down": down},
    }
    report["markdown"] = _compose_markdown(report)
    return report


def main() -> None:
    """CLI 진입점"""
    parser = argparse.ArgumentParser(description="미국 마감 후 한국 증시 영향 브리프 생성")
    parser.add_argument("--hours", type=int, default=24, help="뉴스 조회 시간 범위")
    parser.add_argument("--news-per-source", type=int, default=10, help="뉴스 소스별 최대 기사 수")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument("--output", help="결과를 파일로 저장")
    parser.add_argument("--schedule", action="store_true", help="매일 KST 06:00에 자동 실행")
    parser.add_argument(
        "--schedule-hour",
        type=int,
        default=int(os.getenv("US_CLOSE_SCHEDULE_HOUR", "6")),
    )
    parser.add_argument(
        "--schedule-minute",
        type=int,
        default=int(os.getenv("US_CLOSE_SCHEDULE_MINUTE", "0")),
    )
    parser.add_argument("--output-dir", default=os.getenv("US_CLOSE_OUTPUT_DIR"))
    parser.add_argument("--telegram", action="store_true", help="텔레그램으로도 전송")
    parser.add_argument("--config", default=os.getenv("US_CLOSE_CONFIG_PATH"), help="JSON 설정 파일 경로")
    parser.add_argument("--feedback-file", help="실현 수익률 JSON 파일로 추천 적중률 계산")
    parser.add_argument("--feedback-report", help="피드백 대상 리포트 JSON 경로")
    parser.add_argument("--feedback-log", help="피드백 JSONL 저장 경로")
    args = parser.parse_args()

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    config = AppConfig.load_default(args.config)

    if args.feedback_file:
        report_path = args.feedback_report or os.path.join(
            args.output_dir or _default_output_dir(),
            "latest.json",
        )
        feedback = run_recommendation_feedback(
            report_path=report_path,
            realized_returns_path=args.feedback_file,
            output_path=args.feedback_log,
        )
        print(json.dumps(feedback, ensure_ascii=False, indent=2))
        return

    if args.schedule:
        asyncio.run(
            schedule_us_close_job(
                hour_kst=args.schedule_hour,
                minute_kst=args.schedule_minute,
                news_window_hours=args.hours,
                news_per_source=args.news_per_source,
                output_dir=args.output_dir,
                telegram=args.telegram,
                config=config,
            )
        )
        return

    report = asyncio.run(
        build_us_close_report(args.hours, args.news_per_source, config=config)
    )
    content = (
        json.dumps(report, ensure_ascii=False, indent=2)
        if args.json
        else report["markdown"]
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(content)
    elif not args.json:
        asyncio.run(save_us_close_report(report, output_dir=args.output_dir))

    if args.telegram:
        asyncio.run(send_report_telegram(report))

    print(content)


if __name__ == "__main__":
    main()
