# SK스퀘어 Daily Price Move Explainer — Migration Prompt
> Claude Sonnet 4.8 / VS Code 새 세션 시작용

---

## 프로젝트 개요

SK스퀘어(402340) 일일 주가 변동 원인을 자동 분석하는 IR 챗봇.
- **경로**: `C:\Users\3100041\Desktop\sk-square\daily-price-explainer\`
- **LLM**: Gemini 2.5 Flash (google-genai SDK)
- **폴백 순서**: `gemini-2.5-flash` → `gemini-2.5-flash-8b` → `gemini-1.5-flash`
- **언어**: Python 3.14 / 환경: SK스퀘어 사내망 (SSL 프록시 우회 적용됨)

---

## 디렉토리 구조

```
daily-price-explainer/
├── main.py                      # 엔트리포인트 (chat / briefing / log / beta)
├── .env                         # API 키 (GEMINI, DART, NAVER, BOK, KRX)
├── requirements.txt
├── agents/
│   └── orchestrator.py          # 핵심: 병렬 데이터 수집 → 컨텍스트 빌드 → LLM 호출
├── memory/
│   ├── cache.py                 # diskcache TTL 캐시
│   └── company_context.py       # SK스퀘어 정적 컨텍스트
├── prompts/
│   └── system_prompt.py         # 시스템 프롬프트 (인과 체계 + anti-pattern 포함)
├── tools/
│   ├── market.py                # SKQ OHLCV, KOSPI, _prev_trading_day()
│   ├── nav.py                   # NAV 계산, 할인율, divergence
│   ├── futures.py               # KOSPI200 선물 basis, 외국인 선물 포지션
│   ├── short.py                 # 공매도 잔고·거래량 (T+2 공시 지연 보정)
│   ├── broker.py                # 창구별 순매수 (거래원)
│   ├── sector.py                # 반도체·IT 섹터 수익률, 경쟁사 비교
│   ├── sector_rotation.py       # ETF 기반 섹터 로테이션 신호
│   ├── macro.py                 # 환율(USD/KRW), 국고채 3Y (BOK ECOS)
│   ├── news.py                  # DART 공시 + Naver 뉴스
│   ├── nxt.py                   # NXT(넥스트레이드) 현재가
│   ├── factor_logger.py         # factor_log.csv 적재 + NAV 할인율 rolling stats
│   └── beta.py                  # OLS 베타 (60거래일 누적 후 활성화)
├── data/
│   ├── factor_log.csv           # 일별 팩터 로그
│   └── atomic_facts.jsonl       # ML 학습용 Atomic Facts (62개)
└── SK스퀘어_Price_Explainer_PRD.docx
```

---

## 실행 방법

```bash
# 대화형 모드
python main.py

# 일일 브리핑
python main.py briefing 20260616

# 팩터 로그 저장 (매일 장 마감 후)
python main.py log 20260616

# OLS 베타 (60거래일 누적 후)
python main.py beta
```

---

## 핵심 아키텍처

```
orchestrator.chat(query, date)
  ├─ collect_data(date)       # asyncio.gather — 10개 툴 병렬 호출
  │    market / nav / futures / short / broker
  │    sector / macro / news / nxt / factor_logger
  ├─ build_context(data)      # 구조화 텍스트 컨텍스트 생성
  └─ _call_llm(system, ctx+query)   # Gemini API (폴백 포함)
```

**캐시**: 모든 tool 결과 diskcache TTL 1800s  
캐시 초기화: `rmdir /s /q .cache`

---

## 핵심 도메인 로직

### NAV 계산
```python
NAV = (HYX 종가 × 146,100,000)
    + (DRM 종가 × 16,430,038)
    + 비상장 자산 4,321,300,000,000  # 분기 고정
    + 순현금 793,700,000,000          # 분기 고정

NAV 할인율 = (NAV - 시가총액) / NAV × 100  # 양수 = 할인(정상)
NAV 기대수익률 = (NAV_today - NAV_prev) / NAV_prev × 100
divergence = SKQ 실제수익률 - NAV 기대수익률
```

**발행주식수**: `131,921,000주` (DART 확인 필요)

### NAV 인과 체계 (중요)
- **할인율은 결과(output), 원인이 아님** — "할인율이 커서 주가가 빠졌다" 금지
- divergence < 0 → SKQ가 NAV보다 더 빠진 것 → 원인 탐색 순서:
  1. 수급 (외국인/기관 순매수)
  2. 섹터/매크로 연동
  3. **공매도** — 잔고 증가 → SKQ 시총 억제 → NAV 불변 → 할인율 확대
  4. **선물 차익거래** — 4-case 해석 (아래)
  5. 잔차 (설명 안 되는 경우)

### 선물 차익거래 4-case 해석
```
basis = 선물가 − 현물가
콘탱고(basis>0) + 현물하락  → 매도차익거래 압력 의심
백워데이션(basis<0) + 현물상승 → 숏커버링 또는 현물 선호 매수 압력
콘탱고(basis>0) + 현물상승  → 정상 프리미엄, 차익거래 압력 없음
백워데이션(basis<0) + 현물하락 → 방향성 매도 또는 헤지
```

### 공매도 (tools/short.py)
- KRX 공매도 잔고: **T+2 공시** → 최근 3영업일 역방향 탐색 루프
- 시그널: 잔고 +5,000주 AND 비율≥1% → "압력" / -5,000주 이하 → "청산"
- pykrx 정확한 함수명: `get_shorting_balance_by_date()`, `get_shorting_volume_by_date()`

### NAV Rolling Stats
- `get_discount_stats(windows=[20,60])` → 20/60일 rolling mean/min/max + 5일 slope
- 5거래일 미만이면 historical stats 생략

---

## .env 구성

```
GEMINI_API_KEY=...
DART_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
BOK_API_KEY=...
KRX_ID=...
KRX_PW=...
COMPANY_NAME=SK스퀘어
COMPANY_TICKER=402340
CACHE_DIR=.cache
```

---

## Atomic Facts (data/atomic_facts.jsonl)

ML fine-tuning용. 62개, 7개 L1 카테고리:

| L1 | 범주 | 주요 내용 |
|----|------|-----------|
| F1 | NAV·할인율 구조 | NAV 정의, 할인율, divergence, 인과 방향성 |
| F2 | 수급 | 외국인·기관·공매도 |
| F3 | 파생·차익거래 | 선물 basis, 4-case 해석 |
| F4 | 매크로·섹터 | 환율, 금리, 반도체 섹터 |
| F5 | 자회사 연동 | HYX·DRM 가격 전달 메커니즘 |
| F6 | 기업 이벤트 (corporate event) | 애널리스트 목표주가, 자사주 취득, 대량보유ㆍ블록딜 |
| F7 | 출력 형식 | 표현 규칙, anti-pattern |

fact_type: D(정의) C(인과) S(구조) I(해석) R(제약) Q(정량)

---

## Known Issues & Pending

| # | 항목 | 상태 |
|---|------|------|
| 1 | **공매도 잔고율 N/A** — `_find_col(["잔고율","공매도비율","비율"])` 컬럼 매칭 실패 | ⚠️ 미해결 |
| 2 | **OLS 베타** — 60거래일 누적 필요 (`python main.py log` 매일 실행) | ⏳ 진행중 |
| 3 | **Atomic Facts → RAG/Fine-tuning 파이프라인** 설계 | 🔜 다음 작업 |
| 4 | **shares_outstanding** 131,921,000주 — DART 공식 확인 필요 | ⚠️ 미확인 |

### 공매도 잔고율 컬럼 진단 명령
```bash
python -c "from pykrx import stock; df=stock.get_shorting_balance_by_date('20260611','20260611','402340'); print(df.columns.tolist()); print(df.iloc[0])"
```

---

## 사내망 SSL 우회

모든 tool 파일 상단에 공통 패치 적용:
```python
ssl._create_default_https_context = ssl._create_unverified_context
requests.Session.request  # verify=False 패치
```
