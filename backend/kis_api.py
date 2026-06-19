import asyncio
"""
한국투자증권 OpenAPI 래퍼
REST API + WebSocket 시세
공식 문서: https://apiportal.koreainvestment.com
"""

import aiohttp
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class KISApi:
    # 실전 / 모의 도메인
    REAL_URL = "https://openapi.koreainvestment.com:9443"
    MOCK_URL = "https://openapivts.koreainvestment.com:29443"

    def __init__(self, app_key: str, app_secret: str, account_no: str, is_mock: bool = True):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no          # "50123456-01" 형식
        self.account_prefix = account_no.split("-")[0]
        self.account_suffix = account_no.split("-")[1] if "-" in account_no else "01"
        self.is_mock = is_mock
        self.base_url = self.MOCK_URL if is_mock else self.REAL_URL
        self.access_token: Optional[str] = None
        self.token_expires: Optional[datetime] = None

    # ── 인증 ────────────────────────────────────────────────
    async def get_access_token(self) -> dict:
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if "access_token" not in data:
                    raise ValueError(f"토큰 발급 실패: {data}")
                self.access_token = data["access_token"]
                # 만료 시각 파싱
                expires_str = data.get("access_token_token_expired", "")
                try:
                    self.token_expires = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    self.token_expires = datetime.now() + timedelta(hours=24)
                logger.info("액세스 토큰 발급 완료")
                return data

    async def _ensure_token(self):
        if self.access_token is None or (
            self.token_expires and datetime.now() >= self.token_expires
        ):
            await self.get_access_token()

    def _headers(self, tr_id: str, extra: dict = None) -> dict:
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            headers.update(extra)
        return headers

    async def _get(self, path: str, tr_id: str, params: dict,
                   _retry: int = 0) -> dict:
        await self._ensure_token()
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(tr_id), params=params) as resp:
                data = await resp.json()
                if data.get("rt_cd") != "0":
                    msg = data.get("msg1", "")
                    # 초당 거래건수 초과 → 0.5초 대기 후 최대 3회 재시도
                    if "초당" in msg and _retry < 3:
                        await asyncio.sleep(0.5 * (_retry + 1))
                        return await self._get(path, tr_id, params, _retry + 1)
                    raise ValueError(f"API 오류: {msg or data}")
                return data

    async def _post(self, path: str, tr_id: str, body: dict,
                    _retry: int = 0) -> dict:
        await self._ensure_token()
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(tr_id), json=body) as resp:
                data = await resp.json()
                if data.get("rt_cd") != "0":
                    msg = data.get("msg1", "")
                    if "초당" in msg and _retry < 3:
                        await asyncio.sleep(0.5 * (_retry + 1))
                        return await self._post(path, tr_id, body, _retry + 1)
                    raise ValueError(f"API 오류: {msg or data}")
                return data

    # ── 현재가 조회 (FHKST01010100) ─────────────────────────
    async def get_current_price(self, symbol: str) -> dict:
        data = await self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": symbol},
        )
        o = data["output"]
        return {
            "symbol": symbol,
            "name": o.get("hts_kor_isnm", ""),
            "price": int(o.get("stck_prpr", 0)),
            "change": int(o.get("prdy_vrss", 0)),
            "change_pct": float(o.get("prdy_ctrt", 0)),
            "volume": int(o.get("acml_vol", 0)),
            "high": int(o.get("stck_hgpr", 0)),
            "low": int(o.get("stck_lwpr", 0)),
            "open": int(o.get("stck_oprc", 0)),
            "stock_status_code": o.get("iscd_stat_cls_code", ""),
            "management_issue_code": o.get("mang_issu_cls_code", ""),
            "trading_halt_yn": o.get("trht_yn", ""),
            "timestamp": datetime.now().isoformat(),
        }

    # ── 일봉/분봉 OHLCV (FHKST03010100) ────────────────────
    async def get_ohlcv(self, symbol: str, period: str = "D", count: int = 60) -> list:
        today = datetime.now().strftime("%Y%m%d")
        data = await self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": symbol,
                "fid_input_date_1": "19000101",
                "fid_input_date_2": today,
                "fid_period_div_code": period,
                "fid_org_adj_prc": "1",
            },
        )
        result = []
        for row in data.get("output2", [])[:count]:
            result.append({
                "date": row.get("stck_bsop_date", ""),
                "open": int(row.get("stck_oprc", 0)),
                "high": int(row.get("stck_hgpr", 0)),
                "low": int(row.get("stck_lwpr", 0)),
                "close": int(row.get("stck_clpr", 0)),
                "volume": int(row.get("acml_vol", 0)),
            })
        return list(reversed(result))   # 오래된 것 → 최신 순

    # ── 당일 분봉 OHLCV (FHKST03010200) ────────────────────
    async def get_intraday_ohlcv(self, symbol: str, count: int = 120,
                                 input_hour: str = "") -> list:
        """
        국내주식 당일 분봉 조회.
        KIS 문서의 주식당일분봉조회(inquire-time-itemchartprice)를 사용합니다.
        """
        if count <= 0:
            return []

        def to_int(row: dict, *keys: str) -> int:
            for key in keys:
                value = row.get(key)
                if value not in (None, ""):
                    try:
                        return int(float(str(value).replace(",", "")))
                    except Exception:
                        pass
            return 0

        def normalize_time(value: str) -> str:
            digits = "".join(ch for ch in str(value or "") if ch.isdigit())
            if not digits:
                return ""
            if len(digits) <= 4:
                return digits.zfill(4) + "00"
            return digits.zfill(6)[:6]

        def previous_input_hour(date: str, time: str) -> str:
            try:
                cursor_dt = datetime.strptime(f"{date}{normalize_time(time)}", "%Y%m%d%H%M%S")
                return (cursor_dt - timedelta(seconds=1)).strftime("%H%M%S")
            except Exception:
                return ""

        today = datetime.now().strftime("%Y%m%d")
        cursor_hour = normalize_time(input_hour) if input_hour else datetime.now().strftime("%H%M%S")
        bars_by_key: dict[str, dict] = {}
        max_pages = max(1, min(20, (count // 30) + 8))

        for _ in range(max_pages):
            data = await self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                tr_id="FHKST03010200",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": cursor_hour,
                    "FID_PW_DATA_INCU_YN": "N",
                    "FID_ETC_CLS_CODE": "",
                },
            )

            rows = data.get("output2") or data.get("output") or []
            if not rows:
                break

            before_count = len(bars_by_key)
            oldest_date = ""
            oldest_time = ""

            for row in rows:
                date = row.get("stck_bsop_date") or row.get("bsop_date") or today
                time = normalize_time(row.get("stck_cntg_hour") or row.get("cntg_hour") or row.get("stck_cntg_time") or "")
                if not time:
                    continue

                key = f"{date}{time}"
                if not oldest_date or key < f"{oldest_date}{oldest_time}":
                    oldest_date = date
                    oldest_time = time

                close = to_int(row, "stck_prpr", "stck_clpr", "close")
                open_price = to_int(row, "stck_oprc", "open") or close
                high = to_int(row, "stck_hgpr", "high") or close
                low = to_int(row, "stck_lwpr", "low") or close
                volume = to_int(row, "cntg_vol", "acml_vol", "volume")
                bars_by_key[key] = {
                    "date": date,
                    "time": time,
                    "datetime": key,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "turnover": close * volume,
                }

            if len(bars_by_key) >= count or len(bars_by_key) == before_count or not oldest_date:
                break

            next_cursor = previous_input_hour(oldest_date, oldest_time)
            if not next_cursor or next_cursor == cursor_hour:
                break
            cursor_hour = next_cursor

        result = [bars_by_key[key] for key in sorted(bars_by_key)]
        return result[-count:]

    # ── 현재 호가/예상체결 (FHKST01010200) ───────────────────
    async def get_orderbook(self, symbol: str) -> dict:
        data = await self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            tr_id="FHKST01010200",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        output = data.get("output1") or {}

        def to_int(*keys: str) -> int:
            for key in keys:
                value = output.get(key)
                if value not in (None, ""):
                    try:
                        return int(float(str(value).replace(",", "")))
                    except Exception:
                        pass
            return 0

        total_bid = to_int("total_bidp_rsqn", "total_bidp잔량", "total_bid_qty")
        total_ask = to_int("total_askp_rsqn", "total_askp잔량", "total_ask_qty")
        return {
            "symbol": symbol,
            "total_bid_qty": total_bid,
            "total_ask_qty": total_ask,
            "bid_ask_ratio": (total_bid / total_ask) if total_ask else 0,
            "timestamp": datetime.now().isoformat(),
        }

    async def get_investor_trend(self, symbol: str, days: int = 5) -> dict:
        """최근 투자자별 순매수. 외국인 순매수 조건에 사용."""
        data = await self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        rows = data.get("output", [])[:max(days, 1)]

        def to_int(v):
            try:
                return int(str(v).replace(",", "").strip() or 0)
            except Exception:
                return 0

        foreign = [to_int(r.get("frgn_ntby_qty", 0)) for r in rows]
        personal = [to_int(r.get("prsn_ntby_qty", 0)) for r in rows]
        institution = [to_int(r.get("orgn_ntby_qty", 0)) for r in rows]
        return {
            "symbol": symbol,
            "days": len(rows),
            "foreign_net_qty": sum(foreign),
            "personal_net_qty": sum(personal),
            "institution_net_qty": sum(institution),
            "latest_date": rows[0].get("stck_bsop_date", "") if rows else "",
        }

    async def get_financial_growth(self, symbol: str, quarter: bool = True) -> dict:
        """최근 재무비율 성장률. 최근 실적 성장 조건에 사용."""
        data = await self._get(
            "/uapi/domestic-stock/v1/finance/financial-ratio",
            tr_id="FHKST66430300",
            params={
                "FID_DIV_CLS_CODE": "1" if quarter else "0",
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": symbol,
            },
        )
        rows = data.get("output", [])
        row = rows[0] if rows else {}

        def to_float(v):
            try:
                return float(str(v).replace(",", "").strip() or 0)
            except Exception:
                return 0.0

        return {
            "symbol": symbol,
            "period": row.get("stac_yymm", ""),
            "sales_growth": to_float(row.get("grs", 0)),
            "operating_profit_growth": to_float(row.get("bsop_prfi_inrt", 0)),
            "net_income_growth": to_float(row.get("ntin_inrt", 0)),
        }

    # ── 잔고 조회 (TTTC8434R / VTTC8434R) ──────────────────
    async def get_balance(self) -> dict:
        tr_id = "VTTC8434R" if self.is_mock else "TTTC8434R"
        data = await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_suffix,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        o2 = data.get("output2", [{}])[0]
        return {
            "total_eval": int(o2.get("tot_evlu_amt", 0)),
            "purchase_amount": int(o2.get("pchs_amt_smtl_amt", 0)),
            "profit_loss": int(o2.get("evlu_pfls_smtl_amt", 0)),
            "profit_loss_pct": float(o2.get("evlu_erng_rt", 0)),
            "cash": int(o2.get("dnca_tot_amt", 0)),
            "available_cash": int(o2.get("nass_amt", 0)),
        }

    # ── 보유 종목 ────────────────────────────────────────────
    async def get_positions(self) -> list:
        tr_id = "VTTC8434R" if self.is_mock else "TTTC8434R"
        data = await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_suffix,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        result = []
        for row in data.get("output1", []):
            qty = int(row.get("hldg_qty", 0))
            if qty == 0:
                continue
            result.append({
                "symbol": row.get("pdno", ""),
                "name": row.get("prdt_name", ""),
                "qty": qty,
                "avg_price": int(float(row.get("pchs_avg_pric", 0))),
                "current_price": int(row.get("prpr", 0)),
                "eval_amount": int(row.get("evlu_amt", 0)),
                "profit_loss": int(row.get("evlu_pfls_amt", 0)),
                "profit_loss_pct": float(row.get("evlu_erng_rt", 0)),
            })
        return result

    # ── 주문 ─────────────────────────────────────────────────
    async def place_order(self, symbol: str, side: str, qty: int, price: int = 0) -> dict:
        # 시장가: ORD_DVSN=01, price=0 / 지정가: ORD_DVSN=00
        if side.upper() == "BUY":
            tr_id = "VTTC0802U" if self.is_mock else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.is_mock else "TTTC0801U"

        ord_dvsn = "01" if price == 0 else "00"
        body = {
            "CANO": self.account_prefix,
            "ACNT_PRDT_CD": self.account_suffix,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price) if price > 0 else "0",
        }
        data = await self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id=tr_id,
            body=body,
        )
        o = data.get("output", {})
        return {
            "order_no": o.get("ODNO", ""),
            "order_time": o.get("ORD_TMD", ""),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "timestamp": datetime.now().isoformat(),
        }

    # ── 체결/미체결 주문 조회 ─────────────────────────────────
    async def get_orders(self) -> list:
        tr_id = "VTTC8001R" if self.is_mock else "TTTC8001R"
        data = await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            tr_id=tr_id,
            params={
                "CANO": self.account_prefix,
                "ACNT_PRDT_CD": self.account_suffix,
                "PDNO": "",
                "ORD_UNPR": "0",
                "ORD_DVSN": "01",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        return data.get("output", [])
    # ── 종목명으로 종목코드 검색 (PDNO 검색 API) ─────────────────
    async def search_symbol(self, query: str) -> list:
        """
        종목명 또는 코드 일부로 종목 검색
        KIS API: 국내주식 기본조회 - 종목검색
        """
        await self._ensure_token()
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/search-stock-info"
        params = {
            "PRDT_TYPE_CD": "300",   # 주식
            "PDNO": query,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers("CTPF1002R"), params=params) as resp:
                data = await resp.json()

        results = []
        for item in data.get("output", []):
            code = item.get("pdno", "")
            name = item.get("prdt_name", "")
            if code and name:
                results.append({"symbol": code, "name": name})
        return results[:20]

    async def search_symbol_fallback(self, query: str) -> list:
        """
        KIS API 검색 실패 시 로컬 종목 리스트에서 검색 (오프라인 fallback)
        """
        from screener import KOSPI200, KOSDAQ150
        all_stocks = list(KOSPI200) + list(KOSDAQ150)
        query = query.strip().lower()
        results = []
        for code, name in all_stocks:
            if query in name.lower() or query in code.lower():
                results.append({"symbol": code, "name": name})
        return results[:20]
