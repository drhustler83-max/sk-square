"""
Daily Cache — diskcache 기반
당일 API 응답 캐싱으로 중복 호출 방지

TTL 정책:
- 장 중 (09:00~15:30 KST): 실시간 툴 5분, 정적 데이터 30분
- 장 외 시간: 6시간 (당일 데이터 재사용)
"""
import os
import diskcache as dc
from datetime import datetime
from loguru import logger

import pytz

_cache = None

# 장 중 짧은 TTL 적용 대상 툴
_REALTIME_TOOLS = {"market", "nxt", "broker", "nav"}
_REALTIME_TTL   = 5 * 60      # 5분
_SEMISTATIC_TTL = 30 * 60     # 30분 (뉴스·매크로·섹터 등)
_CLOSED_TTL     = 6 * 60 * 60 # 6시간 (장 외)


def _is_market_hours() -> bool:
    kst = pytz.timezone("Asia/Seoul")
    now = datetime.now(kst)
    total_min = now.hour * 60 + now.minute
    return 9 * 60 <= total_min < 15 * 60 + 30


def _ttl_for(tool: str) -> int:
    if not _is_market_hours():
        return _CLOSED_TTL
    if tool in _REALTIME_TOOLS:
        return _REALTIME_TTL
    return _SEMISTATIC_TTL


def get_cache() -> dc.Cache:
    global _cache
    if _cache is None:
        cache_dir = os.getenv("CACHE_DIR", ".cache")
        _cache = dc.Cache(cache_dir)
    return _cache


def cache_get(key: str):
    return get_cache().get(key)


def cache_set(key: str, value, ttl: int = None):
    tool = key.split(":")[0]
    if ttl is None:
        ttl = _ttl_for(tool)
    get_cache().set(key, value, expire=ttl)
    logger.debug(f"Cached: {key} (TTL {ttl}s)")


def make_key(tool: str, **kwargs) -> str:
    parts = [tool] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
    return ":".join(parts)
