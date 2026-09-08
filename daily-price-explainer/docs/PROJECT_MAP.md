# DPE 프로젝트 지도

> 오래 비운 뒤 돌아왔을 때 **이 문서 하나만** 열면 되도록 만든 문서.
> 최종 갱신: 2026-09-08

---

## 0. 30초 요약

- **DPE(Daily Price Explainer)** = SK스퀘어(402340) 주가가 **전일 종가 대비** 왜 움직였는지 답하는 IR 매니저용 챗봇.
- 현재 확인되는 실행 진입점은 **CLI (`main.py`)**. Telegram 배포는 아직 목표 단계.
- **Phase 1 (완료)**: 팩터로 잔차 설명 → 성공. 뉴스로 잔차 설명 → 실패.
- **Phase 2 (기획 중)**: 애널리스트 목표주가.
- **파일 3개만 기억하면 됩니다**: `data/factor_log.csv`(데이터) · `tools/features.py`(피처 정의) · `output/*.json`(실험 결과)

---

## 1. 폴더 역할

| 폴더 | 역할 |
|---|---|
| `agents/` | `orchestrator.py` — 수집 → 컨텍스트 조립 → Gemini 호출 |
| `prompts/` | `system_prompt.py` — 챗봇 응답 규칙·서술 템플릿 |
| `memory/` | `company_context.py`(회사·보유자산 설정), `cache.py`(SQLite 캐시) |
| `tools/` | 수집·팩터·모델·실험 전부. 32개 모듈 |
| `data/` | 원천·마스터 데이터. **폴더 용량의 98%가 여기** (애널리스트 PDF 118 MB) |
| `output/` | 실험 산출물만. **코드는 넣지 않는다** |
| `schemas/` | 설계 문서 — 뉴스 속성 스키마 + **F6 구조화 이벤트 프로토콜** |
| `scripts/` | 평일 16:35 팩터 적재 스케줄러 등록 (`.bat` / `.xml`) |
| `docs/` | 보고서·기획 문서 (이 문서 포함) |
| `tests/` | 테스트. 실행: `python -m unittest discover -s tests -t .` (루트에서). 현재 10건 통과 |
| `_archive/` | 폐기 후보 격리. 지우지 않고 여기 모아둠 |
| `.cache/` | 시세 캐시(SQLite). gitignore 대상 |
| `.githooks/` | `post-commit` 훅 |

---

## 2. 루트 파일

| 파일 | 무엇인지 |
|---|---|
| `main.py` | CLI 진입점. 챗봇 실행, `analyst --targets` 등 서브커맨드 |
| `atomic_facts.jsonl` | F1~F7 taxonomy 사실 62개. 챗봇 근거 지식 |
| `requirements.txt` | 의존성 |
| `.env` | **비밀키** (DART_API_KEY 등). 커밋 금지 |
| `.env.example` | 키 템플릿 |
| `.gitignore` | 제외 규칙 |
| `README.md` | 프로젝트 개요 |

---

## 3. 핵심 데이터 (`data/`)

| 파일 | 크기 | 무엇인지 |
|---|---|---|
| `factor_log.csv` | 165 KB | **마스터 시계열.** 1,153행 (2021-11-29 ~ 2026-09-07). `date` + 22개 원천 팩터. 평일 16:35 자동 적재 |
| `event_residual_unbiased.csv` | 120 KB | 뉴스 검증용 **1:1 매칭 표본**. `sampling_role`, `matched_pair_id` 포함 |
| `event_residual_biased.csv` | 67 KB | 매칭 이전의 구 표본 (고잔차일 편중). 재현 기록용 |
| `news_property_codex_batch.jsonl` | 1.39 MB | **880거래일 뉴스 속성 추출 결과.** 재생성 비용이 가장 큰 자산 |
| `news_search_log_codex.jsonl` | 186 KB | 뉴스 검색 수행 기록 |
| `news_property_queue.jsonl` | 16 KB | 속성 추출 대기열 |
| `news_search_remaining.csv` | 399 B | 미완료 표기 10일. ⚠ 아래 §6-③ 참조 |
| `.processed_reports.json` | 22 KB | 리포트 처리 추적 (구 처리기용) |
| `analyst_reports/` | **118.57 MB** | 애널리스트 PDF **127개** (13개 증권사, 108개 발간일). Phase 2 원재료 |
| `snapshots/20260729/` | 120 KB | 동결 스냅샷 (재현용) |
| `과거 데이터 참고/` | 310 KB | 구 파이프라인 잔재 + **자사주 History.xlsx**(7배치 이력) + 미팅 필기 사진 2장 |

---

## 4. 실험 산출물 대응표 (`output/`)

**표본이 다른 두 실험이 있습니다. 수치를 섞어 인용하면 안 됩니다.**

| | 실험 A | 실험 B |
|---|---|---|
| 파일 | `unbiased_dl_baseline.json` | `residual_news_experiment.json` |
| 코드 | `tools/experiment_unbiased_dl.py` | `tools/experiment_residual_news.py` |
| 표본 | 전 기간, OOS 651일 | 매칭 표본, OOS 493일 |
| 목적 | 팩터 유의성 + 딥러닝 비교 | 뉴스 증분 기여 검증 |
| 숫자 XGBoost R² | **0.2141** | **0.2811** |
| 부호정확도 | 0.7097 | 0.7140 |

기타: `residual_news_feature_importance.csv`(뉴스 84개 중요도) · `residual_news_oos_predictions.csv` · `event_residual_unbiased_with_news_properties.xlsx`(실험 B 입력 워크북) · `events_with_effective_date.csv` · `regime_{same,cross,zero}.csv`

---

## 5. ⚠ 건드리면 안 되는 것

**① 원격 백업이 없습니다.** git 루트는 상위 폴더 `sk-square/`이고, **58커밋이 원격에 푸시되지 않은 상태**입니다.

**② `.gitignore`로 제외된 것은 git에 없습니다 → 지우면 영구 소실.**

| 대상 | 용량 | 재생성 |
|---|---|---|
| `data/analyst_reports/*.pdf` 127개 | 118.57 MB | ❌ 수집 다시 해야 함 |
| `.cache/` | ~3.5 MB | ✅ 가능 (API 재호출 비용 발생) |
| `*.log`, `__pycache__/` | — | ✅ 자동 |

**③ 재실행 금지 스크립트**: `tools/build_event_residual.py` — 수기 큐레이션 결과를 통째로 덮어씁니다. `tools/repair_event_residual_metrics.py`도 동일 계열이니 확인 후 실행.

---

## 6. 알려진 정합성 이슈 (Phase 2 착수 전 처리 대상)

| # | 이슈 | 위치 | 심각도 |
|---|---|---|---|
| ① | 프롬프트가 **뉴스를 원인 후보 ①번**으로 지정 — Phase 1 결론(뉴스 방향 서술 금지)과 정면 충돌 | `prompts/system_prompt.py:66` | 🔴 높음 |
| ② | 같은 파일에서 **divergence 부호 정의가 반대**. 문서는 `(기대 − 실제)`, 코드는 `skq_ret − nav_implied_ret`(실제 − 기대) | `prompts/system_prompt.py:64` | 🟡 중간 |
| ③ | 검증된 모델과 **다른 설정**을 씀 — 37피처 + `skq_ret` 타겟, 누설 차단 7개 미적용. 그대로 연결하면 검증 안 된 모델 배포 | `tools/attribution.py` | 🔴 높음 |
| ④ | `analyst --targets`가 **구 처리기**를 호출. 신 처리기(`target_report_processor.py`)로 연결 안 됨 | `main.py` | 🟡 중간 |
| ⑤ | 완료된 10일이 미완료로 남아 실험이 876일 → **821일만** 사용 | `data/news_search_remaining.csv` | 🟡 중간 |
| ⑥ | 최근 8행에서 **선물 4개 컬럼·외국인 지분율·개인 순매수 결측**. '미수집'과 '선물 미상장'이 섞일 수 있음 | `data/factor_log.csv` | 🟡 중간 |
| ⑦ | F6 정의가 낡음("시장구조/거래량") — 실제 taxonomy("기업 이벤트")와 충돌 | `docs/MIGRATION_PROMPT.md` | 🟢 낮음 |
| ⑧ | **`data/event_residual.csv`가 현재 없습니다.** `_biased`/`_unbiased`로 분화된 것으로 보이나 미확인. 수기 큐레이션 마스터의 행방 확인 필요 | `data/` | 🟡 확인 필요 |
| ⑨ | H1 회귀 결정계수가 문서 간 불일치 — 보고서 **0.623**, 초기 브리핑 **0.85** | 재현 스크립트 없음 | 🟡 중간 |
| ⑩ | 숫자 피처 30개의 SHAP·순열 중요도가 산출물로 없음 (뉴스 84개는 있음) | `output/` | 🟡 중간 |

---

## 7. Phase 1 결론 + 운영 금지 규칙

보고서: [DPE_Phase1_Report.html](./DPE_Phase1_Report.html)

**모델 고도화 4단계**: OLS(팩터 정의) ✅ → XGBoost·SHAP(유의성 검증) ✅ → 딥러닝 MLP ❌ → 뉴스 속성 투입 ❌

**뉴스 실패의 정체**: 84개 피처 중 가장 안정적인 것이 `news_availability_partial_text` — **기사 본문 확보 여부를 나타내는 수집 상태 플래그**였습니다. 61개(72.6%)는 어느 검증 구간에서도 기여가 없고, 50개는 트리가 한 번도 분기에 쓰지 않았습니다. 모델은 뉴스 내용이 아니라 **뉴스를 어떻게 수집했는지**를 학습했습니다.

**챗봇 금지 규칙 3개** (아직 코드에 적용되지 않음 — §6-①):
1. 뉴스 기반 방향 서술 금지 ("이 뉴스 때문에 올랐다")
2. 뉴스 개수·감성 점수를 근거로 인용 금지
3. 발생 확률 절대값 보고 금지 (매칭 표본 확률 ≠ 모집단 확률)

**미해결**: 2025년 11월 국면 전환으로 팩터 모델도 무너짐 (구간 R² 0.454 → −0.135). 원인 미규명.

---

## 8. Phase 2 착수 지점

**연결 고리는 NAV 할인율입니다.** 한국 지주사 애널리스트의 목표주가는 "NAV 산출 → 적정 할인율 적용"으로 만들어집니다. 즉 `divergence`(시장이 실현한 할인율 변화)와 목표주가(애널리스트가 주장하는 적정 할인율)는 **같은 축 위의 두 점**입니다.

```
목표주가 내재 할인율 = 1 − (목표주가 × 그 시점 발행주식수) / NAV
consensus_discount   = 유효 리포트들의 내재 할인율 중앙값 (최신값 carry-forward)
discount_gap         = nav_discount_pct − consensus_discount
```

이 변수는 **매일 값이 있고**(level이라 carry-forward), Phase 1 핵심 동인과 **같은 단위(%)**이며, F6(빈 이벤트 자리)이 아니라 **F1(NAV 계층)**에 들어갑니다. 즉 Phase 2는 새 질문이 아니라 **F1의 심화**입니다.

**두 가설을 분리해야 합니다:**
- **(A) 수준 가설** — 컨센서스 갭이 잔차를 설명하는가. 매일 값 존재 → 컬럼 추가로 즉시 검정. 내생성 약함. **권장 착수점**
- **(B) 이벤트 가설** — 목표주가 변경 시점에 잔차가 벌어지는가. `schemas/f6_event_protocol.md`가 이미 설계해 둠. 단 **내생성 미처리** — 애널리스트는 주가가 오른 뒤 목표가를 올리므로 과거 수익률 대리변수가 됨

**이미 있는 자산:**
- `tools/target_report_processor.py` — 목표주가 JSON 추출, 상향/하향 숫자 계산, 검수 플래그. 테스트 10개 통과. 단 실행 경로 미연결(§6-④)
- `schemas/f6_event_protocol.md` — point-in-time 규칙(`known_at` vs `economic_at`), 정정 처리, 20일 창 집계 피처 8개 사전 고정
- `tools/shares.py` — **상장주식수 조회** (2026-09-08 신설). 자사주 소각으로 주식수가 변하므로 내재 할인율 계산에 필수
- `data/과거 데이터 참고/자사주 History.xlsx` — 자사주 7배치 발표·매입·완료·소각 이력

**0단계**: PDF 127개 ≠ 목표주가 변경 127건입니다(유지도 많음). **실제 변경 이벤트 수부터 확인**해야 뉴스에서 겪은 "표본 부족으로 유의성 미확보" 함정을 피할 수 있습니다.

**선행 조건**: 과거 NAV에 현재 보유주식수·장부가를 적용하는 경로가 있습니다. 내재 할인율은 NAV로 나누므로 이 버그를 먼저 잡아야 합니다.

---

## 9. 2026-09-08 정리 이력

파일이 없어 보이면 여기를 먼저 확인하십시오. **삭제가 아니라 이동입니다.**

| 원래 위치 | 옮긴 곳 |
|---|---|
| `DPE_Phase1_Report.html`, `COLLAB_BOARD.html`, `SK스퀘어_Price_Explainer_PRD.docx`, `MIGRATION_PROMPT.md`, `DPE 프로젝트.txt`, `Project Dashboard.txt` | `docs/` |
| `test_report_processor.py` | `tests/` |
| `_repro.py`, `_repro2.py`, `_repro3.py`, `_verify.py`, `.cache/_futtest.py`, `output/agent_model_audit_*.py`, `test_futures.py` | `_archive/scripts/` |
| `data/*.log` 8개, `.cache/backfill_*.log` 2개 | `_archive/logs/` |

`test_futures.py`는 이름만 테스트이고 실제로는 **출력 전용 KRX 탐색 스크립트**였습니다. `tests/`에 두면 discovery에 걸려 네트워크 호출이 실행되므로 `_repro*`와 같이 격리했습니다.

**함께 수정한 것:**
- `tools/update_collab_commit_history.py` — `BOARD_PATH`를 `docs/COLLAB_BOARD.html`로 갱신 (이동에 맞춤)
- `tests/__init__.py`, `tests/conftest.py` 신설 — `unittest discover`는 시작 디렉터리가 패키지여야 하고, `conftest.py`는 나중에 pytest를 쓸 때 루트를 import 경로에 넣어준다
- `tools/shares.py` 신설 — `_archive/scripts/_repro3.py`의 상장주식수 조회 로직을 정식 모듈로 승격. `get_listed_shares`, `get_shares_series`, `get_share_reduction_events`(소각 반영일 후보 추출) 제공

**검증:** 이동 후 `python -m unittest discover -s tests -t .` → **10건 전부 통과**

**삭제한 것** (전부 gitignore 대상 = 재생성 가능):
- `__pycache__/` 5곳, `output/codex_io_probe_20260908_*.txt`, 빈 폴더 `.agents/`

**남겨둔 것:** `.cache/cache.db` 계열 4개 — 시세 캐시입니다. 지우면 API 재수집 비용이 발생하고, 파이썬 프로세스가 도는 중에는 `-wal`/`-shm` 삭제가 위험합니다.

**보고서 수정 3건:**
- 그림 A.1의 2차 잔차 지표를 `ROC-AUC 0.506` → `ΔR² −0.111`로 정정 (0.506은 divergence 수준 판별 지표이며 2차 잔차 지표가 아님)
- 뉴스 판별력 타일에 분류 대상 명시
- "Telegram 챗봇" → 현재 진입점은 CLI임을 명시
