"""
NAV v2 공통 계산 — data/nav_daily.csv 기반 point-in-time NAV.

tools/apply_nav_v2.py(과거 일괄 보정)와 tools/factor_logger.py(매일 실시간
적재)가 이 모듈의 compute()를 함께 쓴다. 계산 로직을 한 곳에만 두어, 과거
보정과 실시간 적재가 서로 다른 결과를 내는 일이 없게 한다.

get_live_nav_v2(date)는 factor_logger 전용 진입점이다.
  1. listed_holdings_daily.csv / nav_daily.csv 가 date 를 포함하도록 필요하면
     임시 파일에서 재계산한 뒤 새 거래일만 기존 파일에 덧붙인다. 과거 환율 등
     제공자 값이 바뀌어도 확정된 기존 행은 유지한다.
  2. SK스퀘어 자신과 당일 보유 중인 국내 종목의 종가가 실제 그날 고시된
     값과 일치하는지 재조회해 대조한다(reindex+ffill 로 채워진 전일가를
     정상 시세로 오인하지 않기 위함 — 존재 여부가 아니라 값 자체를 비교).
  3. compute() 로 date 행을 뽑아 반환한다.
  4. 위 어느 단계든 불충분하면 NavDataUnavailable 을 던진다. 호출부(로거)는
     이걸 삼켜 "그날 저장 보류 + 사유 기록"으로만 처리해야 하고, 절대 빈
     값이나 구버전(company_context.py 고정 스냅샷) 계산으로 대신 채워
     저장하면 안 된다 — 그러면 2026-09-22 사고와 다른 방식으로 같은 문제
     (v1/v2 값이 섞여 들어감)가 재발한다.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd
from loguru import logger

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

LISTED_CSV = BASE / "data" / "listed_holdings_daily.csv"
NAV_DAILY = BASE / "data" / "nav_daily.csv"


class NavDataUnavailable(Exception):
    """그 날짜의 NAV v2 값을 신뢰 가능하게 계산할 수 없음.

    factor_logger는 이 예외를 받으면 그 날짜 전체를 저장하지 않고 사유만
    기록해야 한다(부분 저장·v1 대체 계산 금지).
    """


def compute(nav: pd.DataFrame, skq_ret_ref: pd.Series) -> pd.DataFrame:
    """nav_daily 행렬 → factor_log 용 NAV v2 컬럼.

    tools/apply_nav_v2.py에서 옮겨온 순수 계산 함수 — 파일을 읽거나 쓰지
    않는다. 인자로 받은 nav만 보고 계산하므로 과거 일괄 보정과 매일 실시간
    적재 양쪽에서 동일한 결과를 낸다.

    nav_implied_ret 산출 규칙 — 분기 계단 제거
      비상장·순현금은 분기 경계에서 계단으로 바뀌므로, 그대로 차분하면 분기
      첫날마다 가짜 수익률 스파이크가 생긴다. 수익률은 시가평가 변동분만으로
      계산한다: nav_prev_adj(t) = 상장가치(t-1) + 비상장(t) + 순현금(t)
    """
    nav = nav.sort_values("date").reset_index(drop=True)

    close = nav["skq_close"].astype(float)
    skq_ret = ((close / close.shift(1) - 1.0) * 100.0).round(2)
    ref = pd.to_numeric(skq_ret_ref, errors="coerce")
    both = skq_ret.notna() & ref.notna()
    if both.any():
        gap = (skq_ret[both] - ref[both]).abs()
        logger.info(f"skq_ret 대조: 공통 {int(both.sum())}일, "
                    f"최대차 {gap.max():.3f}%p, 0.01 초과 {int((gap > 0.01).sum())}일")

    listed = nav["listed_value_mtm"].astype(float)
    unl = nav["unlisted_value"].astype(float)
    cash = nav["netcash_value"].astype(float)
    total = nav["nav_mtm"].astype(float)

    prev_adj = listed.shift(1) + unl + cash
    implied = (total / prev_adj - 1.0) * 100.0
    implied_raw = (total / total.shift(1) - 1.0) * 100.0  # 참고용: 계단 포함 원시 차분

    disc = nav["nav_discount_pct_mtm"].astype(float)

    out = pd.DataFrame({
        "date": nav["date"].astype(str),
        "nav_total_trillion": (total / 1e12).round(2),
        "nav_implied_ret": implied.round(2),
        "nav_implied_ret_raw": implied_raw.round(2),
        "nav_discount_pct": disc.round(1),
        "nav_discount_delta": disc.diff().round(1),
        "nav_per_share": nav["nav_per_share_mtm"],
        "skq_shares_v2": nav["skq_shares"],
        "skq_ret_v2": skq_ret,
    })
    out["divergence"] = (skq_ret - out["nav_implied_ret"]).round(2)
    return out


def _current_coverage_end() -> str | None:
    """nav_daily.csv에 현재 들어 있는 마지막 날짜. 파일 없으면 None."""
    if not NAV_DAILY.exists():
        return None
    last = None
    with open(NAV_DAILY, encoding="utf-8-sig") as f:
        for last in csv.DictReader(f):
            pass
    return last["date"] if last else None


def _ensure_coverage(date: str) -> None:
    """nav_daily.csv가 date를 포함하지 않으면 파이프라인을 date까지 다시
    만든다. 이미 date까지 있으면 아무것도 하지 않는다(매일 스케줄러가 돌면
    보통 이 경로 — 어제까지 있고 오늘 하루만 부족).

    두 파일(listed_holdings_daily.csv, nav_daily.csv)을 실제 경로에 바로
    쓰지 않는다. 먼저 data/ 바로 밑 임시 폴더에 둘 다 만들고, nav_daily
    쪽에 date가 실제로 들어갔는지 확인한 뒤 기존 행을 유지한 채 새 날짜만
    덧붙여 os.replace로 실제 경로에
    반영한다. 이렇게 안 하면 두 단계 중 하나(주로 두 번째, build_nav)만
    실패했을 때 상장분 CSV는 새 날짜까지 늘어나 있는데 NAV 쪽은 그대로인
    상태로 실제 데이터 폴더가 남는다(2026-09-29 재현·확인됨). 임시 폴더는
    실제 파일과 같은 드라이브(= data/ 바로 밑)에 둬서 os.replace가 항상
    원자적 rename으로 처리되게 한다.
    """
    end = _current_coverage_end()
    if end is not None and end >= date:
        return

    logger.info(f"nav_daily.csv 커버리지 {end!r} → {date} 로 확장 필요, 파이프라인 재실행")
    from tools.build_listed_holdings import build as build_listed
    from tools.build_nav_daily import build as build_nav

    LISTED_CSV.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nav_v2_extend_", dir=str(LISTED_CSV.parent)) as tmp:
        tmp_listed = Path(tmp) / "listed_holdings_daily.csv"
        tmp_nav = Path(tmp) / "nav_daily.csv"

        build_listed(output=tmp_listed, end=date)
        build_nav(output=tmp_nav, listed_csv=tmp_listed)

        check = pd.read_csv(tmp_nav, dtype={"date": str})
        if date not in set(check["date"]):
            raise NavDataUnavailable(
                f"{date}: 파이프라인을 다시 만들었지만 결과에 해당 거래일이 없습니다"
                "(휴장일이거나 확장 실패 — 실제 파일은 손대지 않았습니다)"
            )

        # 전체 재빌드 결과에서는 새 날짜만 취한다. 환율 등 제공자가 과거 값을
        # 수정할 수 있어, 전체 파일을 교체하면 검증·커밋한 역사적 NAV가
        # 조용히 바뀐다(2026-09-28 USD/KRW 수정 실제 확인).
        for old_path, new_path in ((LISTED_CSV, tmp_listed), (NAV_DAILY, tmp_nav)):
            if not old_path.exists():
                continue
            old_bytes, generated = old_path.read_bytes(), new_path.read_bytes()
            old_lines = old_bytes.splitlines(keepends=True)
            new_lines = generated.splitlines(keepends=True)
            if not old_lines or not old_lines[-1].endswith(b"\n"):
                raise NavDataUnavailable(f"기존 파일 줄 끝이 올바르지 않음: {old_path.name}")
            if old_lines[0].rstrip(b"\r\n") != new_lines[0].rstrip(b"\r\n"):
                raise NavDataUnavailable(f"재빌드 컬럼이 기존과 다름: {old_path.name}")
            old_dates = [line.split(b",", 1)[0] for line in old_lines[1:]]
            new_dates = [line.split(b",", 1)[0] for line in new_lines[1:]]
            if (len(old_dates) != len(set(old_dates)) or
                    len(new_dates) != len(set(new_dates)) or
                    new_dates[:len(old_dates)] != old_dates or
                    len(new_dates) <= len(old_dates)):
                raise NavDataUnavailable(f"기존 거래일 순서와 재빌드가 다름: {old_path.name}")
            preserved = old_bytes + b"".join(new_lines[len(old_lines):])
            new_path.write_bytes(preserved)  # 임시 폴더 안에서만 수정

        # 여기까지 왔으면 둘 다 검증됐다 — 이제부터만 실제 경로를 건드린다.
        # 두 파일 교체는 진짜 원자적일 수 없다(os.replace는 파일 단위). 첫 교체
        # 성공 뒤 둘째가 실패하면 listed만 갱신돼 두 파일이 어긋난다(2026-09-30
        # Codex 지적). → 첫 교체 전에 원본 listed를 백업하고, 둘째가 실패하면
        # 첫 교체를 되돌려 "둘 다 반영 or 둘 다 원상복구"를 보장한다.
        # 프로세스가 두 교체 사이에서 강제종료되는 극히 드문 창(listed만 앞선
        # 상태)은 남지만, 자동 복구된다: 다음 확장 때 _current_coverage_end()가
        # nav_daily(뒤처진 날짜)를 읽어 재빌드를 유발하고, build_listed/build_nav가
        # 두 파일을 START부터 통째로 다시 만들어 os.replace로 함께 반영한다.
        # 그 사이 get_live_nav_v2가 호출돼도 nav_daily에 그 날짜가 없어
        # NavDataUnavailable로 안전하게 실패한다(잘못된 값 저장 없음).
        bak_listed = None
        if LISTED_CSV.exists():
            bak_listed = Path(tmp) / "listed_rollback.csv"
            shutil.copy2(LISTED_CSV, bak_listed)
        os.replace(tmp_listed, LISTED_CSV)
        try:
            os.replace(tmp_nav, NAV_DAILY)
        except Exception:
            if bak_listed is not None:
                os.replace(bak_listed, LISTED_CSV)  # 첫 교체 롤백
            raise
    logger.info(f"확장 완료 → {date} (listed_holdings_daily.csv / nav_daily.csv 둘 다 반영)")


def _check_price_freshness(date: str) -> None:
    """listed_holdings_daily.csv/nav_daily.csv에 저장된 당일 종가가 실제
    그날 고시된 값과 일치하는지 재조회해서 확인한다.

    "존재하는지"만 보면 안 된다 — reindex+ffill은 전일 값을 그대로 들고
    내려와도 결측이 아니므로, 값이 있다는 것만으로는 그게 *오늘* 값이라는
    보장이 없다(2026-09-29 Codex 재현: 전일 종가로 채워진 사본을 정상으로
    받아들여 수익률이 +0.30%→0.00%로 계산됨). 그래서 재조회한 원본 종가와
    파이프라인이 실제로 쓴 값을 직접 비교한다.

    SK스퀘어 자기 자신도 검사 대상이다 — divergence의 분자(skq_ret)가
    여기서 나오므로 빠지면 안 된다(직전 버전의 누락).

    IONQ는 검사 대상에서 제외한다 — 설계상 의도적으로 전일 미국 종가를
    쓰므로(한국 장마감 시점엔 당일 미국 장이 아직 안 열림, look-ahead 방지)
    재조회 값과 다른 게 정상이다.
    """
    from tools.build_listed_holdings import HOLDINGS, KR_STOCKS
    from tools.krx_regular import NoRegularTrade, stock_close

    d = pd.Timestamp(date)

    def _currently_held(key: str) -> bool:
        for start, end_, qty, _why, _conf in HOLDINGS[key]:
            s = pd.Timestamp(start)
            e = pd.Timestamp(end_) if end_ else d
            if s <= d <= e and qty > 0:
                return True
        return False

    lf_row = nv_row = None
    if LISTED_CSV.exists():
        lf = pd.read_csv(LISTED_CSV, dtype={"date": str})
        m = lf[lf["date"] == date]
        lf_row = m.iloc[0] if not m.empty else None
    if NAV_DAILY.exists():
        nv = pd.read_csv(NAV_DAILY, dtype={"date": str})
        m = nv[nv["date"] == date]
        nv_row = m.iloc[0] if not m.empty else None

    # (표시명, 티커, 저장된 값이 들어 있는 컬럼, 그 값을 읽을 행)
    checks = [("SK스퀘어", "402340", "skq_close", nv_row)]
    for key, name, ticker in KR_STOCKS:
        if _currently_held(key):
            checks.append((name, ticker, f"{key}_price", lf_row))

    problems = []
    for name, ticker, col, row_src in checks:
        if row_src is None or col not in row_src or pd.isna(row_src[col]):
            problems.append(f"{name}({ticker}): 파이프라인 결과에 저장된 값 없음")
            continue
        used = float(row_src[col])
        try:
            fresh = float(stock_close(ticker, date))
        except NoRegularTrade as exc:
            if ticker == "060570" and "20260731" <= date <= "20260824":
                # 드림어스 거래정지 기간은 병합 전후 가격 기준을 빌더가 처리한다.
                continue
            problems.append(f"{name}({ticker}): {exc}")
            continue
        except Exception as exc:
            raise NavDataUnavailable(f"{date}: {name}({ticker}) KRX 종가 재조회 실패 — {exc}") from exc
        if abs(fresh - used) > 0.5:
            problems.append(
                f"{name}({ticker}): 파이프라인 저장값 {used:,.0f} ≠ 재조회 {fresh:,.0f}"
                "(전일가가 그대로 채워졌을 가능성)"
            )

    if problems:
        raise NavDataUnavailable(
            f"{date}: 당일 시세 검증 실패 — {'; '.join(problems)}. "
            "전일가로 채운 값을 정상 NAV로 저장하지 않기 위해 이 날짜 적재를 보류합니다."
        )


def _hynix_ret(date: str) -> float | None:
    """listed_holdings_daily.csv의 skhynix_price로 하이닉스 당일 수익률 계산."""
    if not LISTED_CSV.exists():
        return None
    lf = pd.read_csv(LISTED_CSV, dtype={"date": str})
    lf = lf.sort_values("date").reset_index(drop=True)
    if date not in set(lf["date"]):
        return None
    pos = int(lf.index[lf["date"] == date][0])
    if pos == 0:
        return None
    today = float(lf.loc[pos, "skhynix_price"])
    prev = float(lf.loc[pos - 1, "skhynix_price"])
    if prev == 0:
        return None
    return round((today / prev - 1.0) * 100.0, 2)


def get_live_nav_v2(date: str) -> dict:
    """factor_logger 전용 진입점.

    date의 NAV v2 값을 반환하거나 NavDataUnavailable을 던진다. 호출부는
    예외 발생 시 그 날짜를 저장하지 않고 사유만 기록해야 한다.
    """
    _ensure_coverage(date)
    _check_price_freshness(date)

    if not NAV_DAILY.exists():
        raise NavDataUnavailable(f"{NAV_DAILY} 없음")

    nav = pd.read_csv(NAV_DAILY, dtype={"date": str})
    nav = nav.sort_values("date").reset_index(drop=True)

    if date not in set(nav["date"]):
        raise NavDataUnavailable(
            f"{date}: nav_daily.csv에 해당 거래일이 없습니다(파이프라인 확장 실패 또는 휴장일)"
        )

    pos = int(nav.index[nav["date"] == date][0])
    if pos == 0:
        raise NavDataUnavailable(f"{date}: 직전 거래일 데이터가 없어 분기보정 수익률을 계산할 수 없습니다")

    computed = compute(nav, pd.Series([None] * len(nav)))
    row = computed[computed["date"] == date]
    if row.empty:
        raise NavDataUnavailable(f"{date}: NAV v2 계산 결과가 없습니다")
    row = row.iloc[0]

    if pd.isna(row["divergence"]) or pd.isna(row["nav_implied_ret"]):
        raise NavDataUnavailable(
            f"{date}: NAV v2 계산 결과가 유효하지 않습니다(divergence/nav_implied_ret 결측)"
        )

    def _f(v):
        return None if pd.isna(v) else float(v)

    return {
        "date": date,
        "skq_ret": _f(row["skq_ret_v2"]),
        "hynix_ret": _hynix_ret(date),
        "nav_total_trillion": _f(row["nav_total_trillion"]),
        "nav_implied_ret": _f(row["nav_implied_ret"]),
        "nav_implied_ret_raw": _f(row["nav_implied_ret_raw"]),
        "divergence": _f(row["divergence"]),
        "nav_discount_pct": _f(row["nav_discount_pct"]),
        "nav_discount_delta": _f(row["nav_discount_delta"]),
        "nav_per_share": _f(row["nav_per_share"]),
        "skq_shares_v2": None if pd.isna(row["skq_shares_v2"]) else int(row["skq_shares_v2"]),
    }
