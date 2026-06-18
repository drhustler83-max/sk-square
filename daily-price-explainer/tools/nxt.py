"""
NXT(넥스트레이드) 현재가 툴
Naver Finance main.naver 페이지의 NXT 탭 파싱
운영시간: 08:00~20:00 (정규 09:00~15:30, 애프터 15:30~18:00)
"""
import re
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


def get_nxt_data(ticker: str) -> dict:
    """
    NXT 현재가 / 등락률 수집
    Returns:
        price:      NXT 현재가 (int)
        change:     전일 대비 변동 (int, +/-)
        pct_change: 등락률 (float)
        direction:  '상승'|'하락'|'보합'
    """
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        today_elems = soup.find_all(class_="today")
        if len(today_elems) < 2:
            logger.warning("NXT today 요소 없음 (NXT 미개장 또는 파싱 실패)")
            return {}

        nxt_elem = today_elems[1]

        # 방향: 텍스트에서 먼저 추출
        full_text = nxt_elem.get_text()
        direction = "상승" if "상승" in full_text else "하락" if "하락" in full_text else "보합"

        # 현재가: no_today 클래스의 두 번째 요소 (중복 없는 숫자)
        no_today_elems = soup.find_all(class_="no_today")
        price = None
        if len(no_today_elems) >= 2:
            price_text = no_today_elems[1].get_text(strip=True)
            # "1,231,0001,231,000" 형태 → 앞 절반만 사용
            nums = re.findall(r"\d[\d,]*", price_text)
            if nums:
                # 가장 짧은 단일 가격 패턴 추출 (중복 제거)
                half = len(price_text) // 2
                price_part = price_text[:half] if len(price_text) > 9 else price_text
                clean = re.search(r"[\d,]+", price_part)
                if clean:
                    price = int(clean.group().replace(",", ""))

        # 등락률
        pct_matches = re.findall(r"(\d+\.\d+)%", full_text)
        # 중복된 값이 나오므로 첫 번째만
        pct = float(pct_matches[0]) if pct_matches else None
        if direction == "하락" and pct:
            pct = -pct

        # 변동폭
        change = None
        change_matches = re.findall(r"([+-]?[\d,]+)원?\s*[lI|]", full_text)
        if not change_matches:
            # 방향 키워드 뒤 숫자
            m = re.search(r"(?:상승|하락)\s*([\d,]+)", full_text)
            if m:
                change_val = int(m.group(1).replace(",", ""))
                change = change_val if direction == "상승" else -change_val

        result = {
            "price": price,
            "change": change,
            "pct_change": pct,
            "direction": direction,
        }
        logger.info(f"NXT {ticker}: {price:,}원 ({direction} {pct:+.2f}%)" if pct else f"NXT {ticker}: {price}")
        return result

    except Exception as e:
        logger.error(f"get_nxt_data error: {e}")
        return {}
