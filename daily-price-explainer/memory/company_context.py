"""
Company Static Context
IR 가이던스, 주요 일정, 경쟁사 정보 등 고정 컨텍스트
→ 실제 운용 시 이 파일을 회사 정보로 업데이트
"""

COMPANY_CONTEXT = {
    "name": "SK스퀘어",
    "ticker": "402340",
    "dart_corp_code": "01596425",  # DART 고유 기업코드
    "sector": "지주/IT",
    "shares_outstanding": 132_087_115,  # 발행주식의 총수 (DART 2025 사업보고서 istc_totqy, 기준일 2025-12-31, 자기주식 210,802 포함)
    "competitors": ["035720", "000660", "005930"],  # 카카오, SK하이닉스, 삼성전자
    "sector_index": {"name": "전기전자", "code": "1013"},  # KRX 참조 섹터지수 (반도체 순수지수 없음 → 전기전자가 하이닉스 포함 대용)

    # NAV 계산용 핵심 보유 자산 (기준: 2026년 1분기말)
    "nav_holdings": [
        # ── 상장 자산 (KRX 실시간 연동) ──
        {
            "name": "SK하이닉스",
            "ticker": "000660",
            "shares_held": 146_100_000,   # 실제 보유주식수
            "listed": True,
        },
        {
            "name": "드림어스컴퍼니",
            "ticker": "090350",
            "shares_held": 16_430_038,    # 실제 보유주식수
            "listed": True,
        },
        # ── 비상장 자산 aggregate (분기 업데이트) ──
        {
            "name": "비상장 자산 합계",
            "ticker": None,
            "book_value": 4_321_300_000_000,  # 4.3213조원 (1Q26 기준)
            "listed": False,
        },
        # ── 순현금 (분기 업데이트) ──
        {
            "name": "순현금",
            "ticker": None,
            "book_value": 793_700_000_000,    # 0.7937조원 (1Q26 기준)
            "listed": False,
        },
    ],
    "nav_last_updated": "2026Q1",  # NAV 고정값 마지막 업데이트 시점
    "key_holdings": [
        "SK하이닉스",
        "드림어스컴퍼니",
        "비상장 자산 (4.32조)",
        "순현금 (0.79조)",
    ],
    "ir_calendar": [
        # {"event": "1Q26 실적발표", "date": "20260515"},
    ],
    "guidance": {
        # 최근 경영진 가이던스 요약
    },
    "key_risks": [
        "SK하이닉스 실적 연동 (NAV 디스카운트)",
        "자회사 IPO 일정 불확실성",
        "IT 업황 사이클",
    ],
}


def get_company_context() -> str:
    """시스템 프롬프트에 주입할 회사 컨텍스트 텍스트 생성"""
    ctx = COMPANY_CONTEXT
    lines = [
        f"## 회사 컨텍스트: {ctx['name']} ({ctx['ticker']})",
        f"- 섹터: {ctx['sector']}",
        f"- 주요 보유 자산: {', '.join(ctx['key_holdings'])}",
        f"- 주요 리스크: {', '.join(ctx['key_risks'])}",
    ]
    if ctx["ir_calendar"]:
        events = [f"{e['event']} ({e['date']})" for e in ctx["ir_calendar"]]
        lines.append(f"- 예정 IR 이벤트: {', '.join(events)}")
    return "\n".join(lines)
