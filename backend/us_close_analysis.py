"""
미국 증시 마감 후 한국 증시 영향 분석기.

외부 유료 데이터 없이 Yahoo Finance 차트 API와 주요 뉴스 RSS를 사용해
한국시간 오전 장 시작 전 참고 가능한 간결한 브리프를 생성한다.
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
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


@dataclass(frozen=True)
class Instrument:
    key: str
    label: str
    symbol: str
    group: str


@dataclass
class MarketMove:
    key: str
    label: str
    symbol: str
    group: str
    price: Optional[float]
    prev_close: Optional[float]
    change_pct: Optional[float]
    fetched_at: str
    error: Optional[str] = None


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    published_at: str
    summary: str = ""


@dataclass
class Recommendation:
    symbol: str
    name: str
    direction: str
    score: float
    reasons: list[str]
    risks: list[str]


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str
    enabled: bool = True


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
        "drivers": ["sox", "xlk", "nvidia", "amd", "tsmc", "nasdaq"],
    },
    {
        "symbol": "000660",
        "name": "SK하이닉스",
        "tags": ["semiconductor", "memory", "hbm"],
        "drivers": ["sox", "nvidia", "amd", "tsmc", "nasdaq"],
    },
    {
        "symbol": "042700",
        "name": "한미반도체",
        "tags": ["semiconductor", "hbm", "equipment"],
        "drivers": ["sox", "nvidia", "amd"],
    },
    {
        "symbol": "403870",
        "name": "HPSP",
        "tags": ["semiconductor", "equipment"],
        "drivers": ["sox", "tsmc", "amd"],
    },
    {
        "symbol": "035420",
        "name": "NAVER",
        "tags": ["growth", "internet"],
        "drivers": ["nasdaq", "xlk", "meta", "amazon"],
    },
    {
        "symbol": "035720",
        "name": "카카오",
        "tags": ["growth", "internet"],
        "drivers": ["nasdaq", "xlk", "meta"],
    },
    {
        "symbol": "373220",
        "name": "LG에너지솔루션",
        "tags": ["battery", "growth"],
        "drivers": ["tesla", "nasdaq"],
    },
    {
        "symbol": "006400",
        "name": "삼성SDI",
        "tags": ["battery", "growth"],
        "drivers": ["tesla", "nasdaq"],
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
]

POSITIVE_WORDS = [
    "rally", "surge", "gain", "record", "beat", "strong", "growth", "optimism",
    "upgrade", "ai", "chip", "semiconductor", "soft landing", "cut rates",
]
NEGATIVE_WORDS = [
    "selloff", "fall", "drop", "plunge", "miss", "weak", "recession", "tariff",
    "inflation", "higher yields", "risk", "downgrade", "war", "sanction",
]


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _fmt_pct(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _score_from_pct(value: Optional[float], scale: float = 1.0) -> float:
    if value is None or not math.isfinite(value):
        return 0.0
    return max(-4.0, min(4.0, value * scale))


class YahooMarketDataClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch_move(self, instrument: Instrument) -> MarketMove:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(instrument.symbol, safe='')}?range=5d&interval=1d"
        )
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={
                    "User-Agent": "Mozilla/5.0 kis-trading/1.0",
                    "Accept": "application/json,text/plain,*/*",
                },
            ) as resp:
                if resp.status >= 400:
                    return self._error(instrument, f"HTTP {resp.status}")
                payload = await resp.json(content_type=None)
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
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
            return MarketMove(
                key=instrument.key,
                label=instrument.label,
                symbol=instrument.symbol,
                group=instrument.group,
                price=round(float(price), 4) if price is not None else None,
                prev_close=round(float(prev_close), 4) if prev_close is not None else None,
                change_pct=change_pct,
                fetched_at=datetime.now(UTC).isoformat(),
            )
        except Exception as exc:
            logger.warning("market fetch failed for %s: %s", instrument.symbol, exc)
            return self._error(instrument, str(exc))

    @staticmethod
    def _error(instrument: Instrument, message: str) -> MarketMove:
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
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch_recent(self, hours: int = 24, per_source: int = 10) -> list[NewsItem]:
        tasks = [
            self._fetch_feed(source, url, hours, per_source)
            for source, url in NEWS_FEEDS.items()
            if url
        ]
        results = await asyncio.gather(*tasks)
        items = [item for group in results for item in group]
        items.sort(key=lambda item: item.published_at, reverse=True)
        return items

    async def _fetch_feed(self, source: str, url: str, hours: int, limit: int) -> list[NewsItem]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "kis-trading/1.0"},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("news fetch failed for %s: HTTP %s", source, resp.status)
                    return []
                text = await resp.text()
            root = ElementTree.fromstring(text)
        except Exception as exc:
            logger.info("news fetch failed for %s: %s", source, exc)
            return []

        items = []
        for node in root.findall(".//item"):
            title = _clean_text(node.findtext("title"))
            link = _clean_text(node.findtext("link"))
            summary = _clean_text(node.findtext("description"))
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
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except Exception:
            return None


def _news_signal(news: list[NewsItem]) -> dict:
    text = " ".join(f"{item.title} {item.summary}" for item in news).lower()
    positive = sum(text.count(word) for word in POSITIVE_WORDS)
    negative = sum(text.count(word) for word in NEGATIVE_WORDS)
    themes = []
    for label, words in {
        "AI/반도체": ["ai", "chip", "semiconductor", "nvidia", "tsmc", "memory"],
        "금리/달러": ["yield", "fed", "rate", "dollar", "inflation"],
        "에너지": ["oil", "wti", "energy", "gas"],
        "전기차/배터리": ["tesla", "ev", "battery"],
        "지정학/관세": ["tariff", "war", "sanction", "china"],
    }.items():
        count = sum(text.count(word) for word in words)
        if count:
            themes.append({"theme": label, "count": count})
    themes.sort(key=lambda item: item["count"], reverse=True)
    return {
        "positive_hits": positive,
        "negative_hits": negative,
        "net_score": positive - negative,
        "themes": themes[:5],
    }


def _build_recommendations(moves: dict[str, MarketMove], news_signal: dict) -> list[Recommendation]:
    recommendations = []
    risk_penalty = 0.0
    if (moves.get("us10y") and moves["us10y"].change_pct or 0) > 1.0:
        risk_penalty -= 0.5
    if (moves.get("dxy") and moves["dxy"].change_pct or 0) > 0.4:
        risk_penalty -= 0.4
    if news_signal["net_score"] < 0:
        risk_penalty -= min(0.8, abs(news_signal["net_score"]) * 0.08)
    if news_signal["net_score"] > 0:
        risk_penalty += min(0.6, news_signal["net_score"] * 0.06)

    for candidate in KOREA_CANDIDATES:
        driver_scores = []
        reasons = []
        risks = []
        for key in candidate["drivers"]:
            move = moves.get(key)
            if not move:
                continue
            pct = move.change_pct
            driver_scores.append(_score_from_pct(pct))
            if pct is not None and abs(pct) >= 0.5:
                reasons.append(f"{move.label} {_fmt_pct(pct)}")

        score = (sum(driver_scores) / len(driver_scores) if driver_scores else 0.0) + risk_penalty

        tags = set(candidate["tags"])
        if "semiconductor" in tags:
            score += _score_from_pct(moves.get("sox").change_pct if moves.get("sox") else None, 0.7)
            if any(t["theme"] == "AI/반도체" for t in news_signal["themes"]):
                score += 0.4
                reasons.append("AI/반도체 뉴스 빈도 증가")
        if "growth" in tags:
            score -= _score_from_pct(moves.get("us10y").change_pct if moves.get("us10y") else None, 0.25)
            if (moves.get("dxy") and moves["dxy"].change_pct or 0) > 0.4:
                risks.append("달러 강세는 외국인 수급 부담")
        if "financial" in tags:
            score += _score_from_pct(moves.get("us10y").change_pct if moves.get("us10y") else None, 0.25)
            risks.append("금리 급락 시 은행 순이자마진 기대 약화")
        if "energy" in tags:
            score += _score_from_pct(moves.get("wti").change_pct if moves.get("wti") else None, 0.8)
            if (moves.get("wti") and moves["wti"].change_pct or 0) < -1:
                risks.append("유가 약세는 정유/에너지 투자심리 부담")
        if "healthcare" in tags and score < 0:
            score += 0.5
            reasons.append("방어주 성격으로 하락장 상대 강도 기대")

        if not reasons:
            reasons.append("미국 마감 데이터와 매크로 신호 종합")
        if (moves.get("nasdaq") and moves["nasdaq"].change_pct or 0) < -1.0 and "growth" in tags:
            risks.append("나스닥 약세 시 성장주 밸류에이션 부담")
        if (moves.get("us10y") and moves["us10y"].change_pct or 0) > 2.0:
            risks.append("미 10년물 금리 급등")

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


def _market_summary(moves: dict[str, MarketMove]) -> list[str]:
    bullets = []
    major_keys = ["sp500", "nasdaq", "dow", "russell2000"]
    sector_keys = ["sox", "xlk", "xle", "xlf", "xlv"]
    major_avg = sum(_score_from_pct(moves[k].change_pct) for k in major_keys if k in moves) / 4
    sector_leader = max(
        (moves[k] for k in sector_keys if k in moves and moves[k].change_pct is not None),
        key=lambda item: item.change_pct,
        default=None,
    )
    sector_lagger = min(
        (moves[k] for k in sector_keys if k in moves and moves[k].change_pct is not None),
        key=lambda item: item.change_pct,
        default=None,
    )
    if major_avg > 0.4:
        bullets.append("미국 주요 지수는 위험선호 우위로 마감했습니다.")
    elif major_avg < -0.4:
        bullets.append("미국 주요 지수는 위험회피 우위로 마감했습니다.")
    else:
        bullets.append("미국 주요 지수는 혼조권으로 마감했습니다.")
    if sector_leader:
        bullets.append(f"섹터 강세는 {sector_leader.label}({_fmt_pct(sector_leader.change_pct)})가 주도했습니다.")
    if sector_lagger:
        bullets.append(f"섹터 약세는 {sector_lagger.label}({_fmt_pct(sector_lagger.change_pct)})가 두드러졌습니다.")
    if moves.get("us10y") and moves["us10y"].change_pct is not None:
        bullets.append(f"미 10년물 금리 변화율은 {_fmt_pct(moves['us10y'].change_pct)}입니다.")
    if moves.get("dxy") and moves["dxy"].change_pct is not None:
        bullets.append(f"DXY는 {_fmt_pct(moves['dxy'].change_pct)}로 외국인 수급에 영향을 줄 수 있습니다.")
    return bullets


def _compose_markdown(report: dict) -> str:
    lines = [
        "# 미국 마감 후 한국 증시 영향 브리프",
        "",
        f"- 생성시각: {report['generated_at_kst']}",
        f"- 뉴스 기준: 최근 {report['news_window_hours']}시간",
        "",
        "## 핵심 요약",
    ]
    lines.extend(f"- {item}" for item in report["summary"])
    lines.extend(["", "## 상승 가능 후보"])
    for item in report["recommendations"]["up"]:
        reason = "; ".join(item["reasons"])
        risk = f" / 리스크: {'; '.join(item['risks'])}" if item["risks"] else ""
        lines.append(f"- {item['name']}({item['symbol']}) score {item['score']}: {reason}{risk}")
    lines.extend(["", "## 하락 위험 후보"])
    for item in report["recommendations"]["down"]:
        reason = "; ".join(item["reasons"])
        risk = f" / 체크: {'; '.join(item['risks'])}" if item["risks"] else ""
        lines.append(f"- {item['name']}({item['symbol']}) score {item['score']}: {reason}{risk}")
    lines.extend(["", "## 주요 데이터"])
    for move in report["market_data"]:
        lines.append(f"- {move['label']}: {_fmt_pct(move['change_pct'])}")
    lines.extend(["", "## 최근 뉴스"])
    for item in report["news"][:10]:
        lines.append(f"- [{item['source']}] {item['title']} ({item['published_at']})")
    return "\n".join(lines)


def _compose_telegram_message(report: dict) -> str:
    lines = [
        "미국 마감 후 한국 증시 브리프",
        f"생성시각: {report['generated_at_kst']}",
    ]
    summary = report.get("summary") or []
    if summary:
        lines.append("")
        lines.append("핵심 요약")
        lines.extend(f"- {item}" for item in summary[:5])

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


def get_telegram_config() -> Optional[TelegramConfig]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("US_CLOSE_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("US_CLOSE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    enabled_value = os.getenv("US_CLOSE_TELEGRAM_ENABLED", "true").lower()
    enabled = enabled_value not in ("0", "false", "no", "off")
    return TelegramConfig(bot_token=token, chat_id=chat_id, enabled=enabled)


async def send_telegram_message(text: str, config: Optional[TelegramConfig] = None) -> dict:
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
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("telegram send failed: HTTP %s %s", resp.status, body[:300])
                    return {"ok": False, "detail": body}
                return {"ok": True, "detail": "sent"}
    except Exception as exc:
        logger.warning("telegram send failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


def _default_output_dir() -> str:
    base_dir = os.path.dirname(__file__)
    return os.getenv("US_CLOSE_OUTPUT_DIR", os.path.join(base_dir, "reports"))


def _ensure_output_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


async def save_us_close_report(report: dict, output_dir: Optional[str] = None) -> dict:
    output_dir = output_dir or _default_output_dir()
    _ensure_output_dir(output_dir)
    latest_json = os.path.join(output_dir, "latest.json")
    latest_md = os.path.join(output_dir, "latest.md")
    history_json = os.path.join(output_dir, f"us-close-{datetime.now(KST).strftime('%Y%m%d-%H%M%S')}.json")
    history_md = os.path.join(output_dir, f"us-close-{datetime.now(KST).strftime('%Y%m%d-%H%M%S')}.md")

    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(latest_md, "w", encoding="utf-8") as f:
        f.write(report["markdown"])
    with open(history_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(history_md, "w", encoding="utf-8") as f:
        f.write(report["markdown"])

    report["saved_to"] = {
        "latest_json": latest_json,
        "latest_md": latest_md,
        "history_json": history_json,
        "history_md": history_md,
    }
    return report


async def run_us_close_job(
    news_window_hours: int = 24,
    news_per_source: int = 10,
    output_dir: Optional[str] = None,
    telegram: bool = False,
) -> dict:
    report = await build_us_close_report(
        news_window_hours=news_window_hours,
        news_per_source=news_per_source,
    )
    report = await save_us_close_report(report, output_dir=output_dir)
    if telegram:
        result = await send_telegram_message(_compose_telegram_message(report))
        report["telegram_sent"] = result.get("ok", False)
        report["telegram_detail"] = result.get("detail")
    return report


async def send_latest_us_close_telegram(
    news_window_hours: int = 24,
    news_per_source: int = 10,
) -> dict:
    report = await build_us_close_report(
        news_window_hours=news_window_hours,
        news_per_source=news_per_source,
    )
    result = await send_telegram_message(_compose_telegram_message(report))
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
) -> None:
    if hour_kst < 0 or hour_kst > 23:
        raise ValueError("hour_kst must be 0..23")
    if minute_kst < 0 or minute_kst > 59:
        raise ValueError("minute_kst must be 0..59")

    while True:
        now = datetime.now(KST)
        next_run = now.replace(hour=hour_kst, minute=minute_kst, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info("US close scheduler sleeping until %s KST (%s seconds)", next_run.isoformat(timespec="seconds"), int(sleep_seconds))
        await asyncio.sleep(sleep_seconds)
        try:
            logger.info("US close scheduler running job at %s KST", datetime.now(KST).isoformat(timespec="seconds"))
            report = await run_us_close_job(
                news_window_hours=news_window_hours,
                news_per_source=news_per_source,
                output_dir=output_dir,
                telegram=telegram,
            )
            logger.info("US close scheduler completed; saved_to=%s", report.get("saved_to"))
        except Exception as exc:
            logger.exception("US close scheduler job failed: %s", exc)


async def build_us_close_report(news_window_hours: int = 24, news_per_source: int = 10) -> dict:
    async with aiohttp.ClientSession() as session:
        market_client = YahooMarketDataClient(session)
        news_client = RssNewsClient(session)
        news_task = asyncio.create_task(
            news_client.fetch_recent(hours=news_window_hours, per_source=news_per_source)
        )
        market_results = []
        for item in INSTRUMENTS:
            market_results.append(await market_client.fetch_move(item))
            await asyncio.sleep(0.15)
        news = await news_task

    moves = {item.key: item for item in market_results}
    signal = _news_signal(news)
    recs = _build_recommendations(moves, signal)
    up = [asdict(item) for item in recs if item.score > 0][:8]
    down = [asdict(item) for item in sorted(recs, key=lambda item: item.score) if item.score < 0][:8]
    report = {
        "generated_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "news_window_hours": news_window_hours,
        "summary": _market_summary(moves),
        "news_signal": signal,
        "market_data": [asdict(item) for item in market_results],
        "news": [asdict(item) for item in news],
        "recommendations": {"up": up, "down": down},
    }
    report["markdown"] = _compose_markdown(report)
    return report



def main() -> None:
    parser = argparse.ArgumentParser(description="미국 마감 후 한국 증시 영향 브리프 생성")
    parser.add_argument("--hours", type=int, default=24, help="뉴스 조회 시간 범위")
    parser.add_argument("--news-per-source", type=int, default=10, help="뉴스 소스별 최대 기사 수")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument("--output", help="결과를 파일로 저장")
    parser.add_argument("--schedule", action="store_true", help="매일 KST 06:00에 자동 실행")
    parser.add_argument("--schedule-hour", type=int, default=int(os.getenv("US_CLOSE_SCHEDULE_HOUR", "6")))
    parser.add_argument("--schedule-minute", type=int, default=int(os.getenv("US_CLOSE_SCHEDULE_MINUTE", "0")))
    parser.add_argument("--output-dir", default=os.getenv("US_CLOSE_OUTPUT_DIR"))
    parser.add_argument("--telegram", action="store_true", help="텔레그램으로도 전송")
    args = parser.parse_args()

    if args.schedule:
        asyncio.run(schedule_us_close_job(
            hour_kst=args.schedule_hour,
            minute_kst=args.schedule_minute,
            news_window_hours=args.hours,
            news_per_source=args.news_per_source,
            output_dir=args.output_dir,
            telegram=args.telegram,
        ))
        return

    report = asyncio.run(build_us_close_report(args.hours, args.news_per_source))
    content = (
        json.dumps(report, ensure_ascii=False, indent=2)
        if args.json
        else report["markdown"]
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(content)
    elif not args.json:
        # 기본 실행 시 파일도 함께 남긴다.
        asyncio.run(save_us_close_report(report, output_dir=args.output_dir))
    if args.telegram:
        asyncio.run(send_telegram_message(_compose_telegram_message(report)))
    print(content)


if __name__ == "__main__":
    main()
