# Daily Price Move Explainer

IR팀을 위한 주가 변동 설명 AI 에이전트

## 프로젝트 구조

```
daily-price-explainer/
├── agents/
│   └── orchestrator.py     # 메인 오케스트레이터 (의도 분류 → 툴 선택 → 답변 합성)
├── tools/
│   ├── market.py           # 주가·수급 데이터 (pykrx)
│   ├── news.py             # 뉴스·DART 공시
│   ├── macro.py            # 환율·금리·글로벌 지수 (BOK ECOS + yfinance)
│   └── sector.py           # 섹터·경쟁사 비교
├── memory/
│   ├── cache.py            # 당일 API 응답 캐싱 (diskcache)
│   └── company_context.py  # 회사 고정 컨텍스트 (IR 가이던스, 경쟁사 등)
├── prompts/
│   └── system_prompt.py    # Claude 시스템 프롬프트
├── tests/                  # 테스트 (추후 작성)
├── main.py                 # 엔트리포인트
├── requirements.txt
└── .env.example
```

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
# .env 파일에 API 키 입력
```

필요한 API 키:
- `ANTHROPIC_API_KEY` — [console.anthropic.com](https://console.anthropic.com)
- `DART_API_KEY` — [opendart.fss.or.kr](https://opendart.fss.or.kr)
- `NAVER_CLIENT_ID/SECRET` — [developers.naver.com](https://developers.naver.com)
- `BOK_API_KEY` — [ecos.bok.or.kr](https://ecos.bok.or.kr)

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

### 3. 실행

```bash
# 대화형 챗봇
python main.py

# 일일 자동 브리핑
python main.py briefing

# 특정 날짜 브리핑
python main.py briefing 20260601
```

## 회사 설정 변경

`memory/company_context.py`에서 분석 대상 회사 정보를 수정하세요.
