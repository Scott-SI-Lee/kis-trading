"""
stock_master.py
KIS 종목 마스터 파일 다운로드 + 파싱 + 인메모리 검색
- KOSPI: https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip
- KOSDAQ: https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip

서버 시작 시 1회 로드, 이후 인메모리에서 초고속 검색 (약 4,000여 종목)
매일 자정 자동 갱신
"""

import asyncio
import aiohttp
import zipfile
import io
import logging
import re
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# ── 다운로드 URL ─────────────────────────────────────────────
KOSPI_MST_URL  = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_MST_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"


# ── MST 파일 파싱 ────────────────────────────────────────────
# KIS 공식 파싱 방식 (고정 바이트 포맷)
# KOSPI: 종목코드(9) + 종목명(40) + ... (나머지)
# KOSDAQ: 종목코드(9) + 종목명(48) + ... (나머지)

def _parse_kospi_mst(raw: bytes) -> list[dict]:
    results = []
    lines = raw.decode("cp949", errors="ignore").split("\n")
    for line in lines:
        if len(line) < 50:
            continue
        try:
            code = line[0:9].strip()
            name = line[9:49].strip()
            # 6자리 숫자 코드만 유효 종목
            if re.match(r"^\d{6}$", code) and name:
                results.append({"symbol": code, "name": name, "market": "KOSPI"})
        except Exception:
            continue
    return results

def _parse_kosdaq_mst(raw: bytes) -> list[dict]:
    results = []
    lines = raw.decode("cp949", errors="ignore").split("\n")
    for line in lines:
        if len(line) < 57:
            continue
        try:
            code = line[0:9].strip()
            name = line[9:57].strip()
            if re.match(r"^\d{6}$", code) and name:
                results.append({"symbol": code, "name": name, "market": "KOSDAQ"})
        except Exception:
            continue
    return results


# ── 마스터 다운로드 ──────────────────────────────────────────
async def _download_and_parse(url: str, parser) -> list[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(f"마스터 다운로드 실패 {url}: HTTP {resp.status}")
                    return []
                data = await resp.read()

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            fname = zf.namelist()[0]
            raw = zf.read(fname)

        stocks = parser(raw)
        logger.info(f"마스터 파싱 완료: {url} → {len(stocks)}종목")
        return stocks
    except Exception as e:
        logger.warning(f"마스터 다운로드 오류 {url}: {e}")
        return []


# ════════════════════════════════════════════════════════════
# StockMaster — 싱글턴 인메모리 종목 DB
# ════════════════════════════════════════════════════════════
class StockMaster:
    def __init__(self):
        self._stocks: list[dict] = []          # {"symbol", "name", "market"}
        self._name_idx: dict[str, list] = {}   # 초성/이름 역색인
        self._code_idx: dict[str, dict] = {}   # 코드 → 종목
        self._loaded_date: Optional[date] = None
        self._loading = False

    async def ensure_loaded(self):
        """아직 로드 안 됐거나 하루 지났으면 갱신"""
        if self._loaded_date == date.today() and self._stocks:
            return
        if self._loading:
            # 다른 코루틴이 로딩 중이면 완료 대기
            while self._loading:
                await asyncio.sleep(0.2)
            return
        await self._load()

    async def _load(self):
        self._loading = True
        logger.info("📥 종목 마스터 다운로드 시작 (KOSPI + KOSDAQ)...")
        try:
            kospi, kosdaq = await asyncio.gather(
                _download_and_parse(KOSPI_MST_URL,  _parse_kospi_mst),
                _download_and_parse(KOSDAQ_MST_URL, _parse_kosdaq_mst),
            )
            all_stocks = kospi + kosdaq

            if not all_stocks:
                logger.warning("⚠️  마스터 다운로드 실패 — 기존 데이터 유지")
                return

            self._stocks = all_stocks
            # 역색인 구성
            self._code_idx = {s["symbol"]: s for s in all_stocks}
            self._name_idx = {}
            for s in all_stocks:
                # 이름을 공백 제거한 소문자로 색인
                key = s["name"].replace(" ", "").lower()
                self._name_idx.setdefault(key, []).append(s)

            self._loaded_date = date.today()
            logger.info(f"✅ 종목 마스터 로드 완료: 총 {len(all_stocks)}종목 "
                        f"(KOSPI {len(kospi)}, KOSDAQ {len(kosdaq)})")
        finally:
            self._loading = False

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """
        종목명 또는 코드로 검색 (인메모리, 즉시 반환)
        - 코드 완전일치 우선
        - 이름 전방일치 다음
        - 이름 부분일치 마지막
        """
        if not self._stocks:
            return []

        q = query.strip()
        q_lower = q.lower()
        q_no_space = q_lower.replace(" ", "")

        results = []
        seen = set()

        def add(item):
            if item["symbol"] not in seen:
                seen.add(item["symbol"])
                results.append(item)

        # 1순위: 코드 완전일치
        if q in self._code_idx:
            add(self._code_idx[q])

        # 2순위: 코드 전방일치
        if len(results) < limit:
            for s in self._stocks:
                if s["symbol"].startswith(q) and s["symbol"] != q:
                    add(s)

        # 3순위: 이름 전방일치 (공백 무시)
        if len(results) < limit:
            for s in self._stocks:
                name_ns = s["name"].replace(" ", "").lower()
                if name_ns.startswith(q_no_space):
                    add(s)

        # 4순위: 이름 부분일치
        if len(results) < limit:
            for s in self._stocks:
                name_ns = s["name"].replace(" ", "").lower()
                if q_no_space in name_ns and not name_ns.startswith(q_no_space):
                    add(s)

        return results[:limit]

    def get_by_code(self, code: str) -> Optional[dict]:
        return self._code_idx.get(code)

    @property
    def total(self) -> int:
        return len(self._stocks)

    @property
    def loaded(self) -> bool:
        return bool(self._stocks)


# 싱글턴
stock_master = StockMaster()


async def schedule_daily_refresh():
    """매일 자정에 종목 마스터 자동 갱신"""
    while True:
        now = datetime.now()
        # 다음 자정까지 대기
        seconds_until_midnight = (
            (24 - now.hour) * 3600 - now.minute * 60 - now.second
        )
        await asyncio.sleep(seconds_until_midnight)
        logger.info("🔄 종목 마스터 일일 갱신 시작")
        await stock_master._load()
