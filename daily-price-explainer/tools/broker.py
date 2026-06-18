"""
Broker Window Tool
Naver Finance main.naver 거래원 테이블 파싱
매도/매수 상위 창구 + 외국계 추정 순매수
"""
import ssl
import urllib3
import requests
from bs4 import BeautifulSoup
from loguru import logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def get_broker_data(ticker: str) -> dict:
    """
    Naver Finance 거래원 정보 수집
    Returns:
        foreign_net: 외국계 추정 순매수 (거래량 단위)
        top_sellers: 매도 상위 창구 리스트 [{name, volume}]
        top_buyers:  매수 상위 창구 리스트 [{name, volume}]
    """
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        tables = soup.find_all("table")
        broker_table = None
        for t in tables:
            cap = t.find("caption")
            if cap and "거래원" in cap.get_text():
                broker_table = t
                break

        if not broker_table:
            logger.warning("거래원 테이블 없음")
            return {}

        rows = broker_table.find_all("tr")
        top_sellers = []
        top_buyers  = []
        foreign_net = None

        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            if len(cells) < 4:
                continue

            # 외국계 추정합 행
            if "외국계" in cells[0]:
                try:
                    sell_vol = int(cells[1].replace(",", "").replace("-", "0"))
                    buy_vol  = int(cells[2].replace(",", "").lstrip("-").replace("-", "0")) if cells[2] else 0
                    # cells[2]가 음수면 외국계 매도 우위
                    raw = cells[2].replace(",", "")
                    foreign_net = -int(raw) if raw.startswith("-") else int(raw)
                except Exception:
                    pass
                continue

            # 일반 창구 행
            sell_name = cells[0].strip()
            buy_name  = cells[2].strip()

            if sell_name and sell_name != "매도상위":
                try:
                    top_sellers.append({
                        "name": sell_name,
                        "volume": int(cells[1].replace(",", "")),
                    })
                except Exception:
                    pass

            if buy_name and buy_name != "매수상위":
                try:
                    top_buyers.append({
                        "name": buy_name,
                        "volume": int(cells[3].replace(",", "")),
                    })
                except Exception:
                    pass

        logger.info(f"거래원 파싱 완료: 매도 {len(top_sellers)}개, 매수 {len(top_buyers)}개, 외국계순매수={foreign_net}")
        return {
            "foreign_net": foreign_net,
            "top_sellers": top_sellers[:5],
            "top_buyers":  top_buyers[:5],
        }

    except Exception as e:
        logger.error(f"get_broker_data error: {e}")
        return {}
