# DPE 모델링 인수인계 — 2026-10-15

> **작성**: Claude Code 세션 2026-10-15. **대상 독자**: 다음 대화창의 Claude Code.
> 데이터 복구는 끝났다. 이 문서는 **모인 데이터를 모델에 적용하는 단계**를 위한 것이다.
> 자기완결적으로 썼으나, 복구 이력의 상세는 `docs/CODEX_HANDOFF_DATA_RESTORE.md` §8에 있다.

---

## 0. 30초 요약

SK스퀘어(KOSPI 402340) IR팀의 주가 변동 설명 챗봇(DPE)용 모델이다.
핵심 타깃은 **`divergence`** — SK스퀘어 주가 수익률에서 NAV 기반 기대수익률을 뺀 잔차다.

**데이터 복구가 완료됐다.** `data/factor_log.csv`는 상장(2021-11-29) 이후
**1,191거래일 전 구간**을 덮고, 과거 진짜 결측이 **0**이다. 뉴스도 상장 다음날부터
2026-10-14까지 전 거래일 전수(1,190일)다.

**이번 단계의 핵심 기회**: Phase 1에서 뉴스 피처를 모델 입력에서 **제외**했던 이유가
`"news was curated almost exclusively on high-residual dates, creating selection
leakage"`(`output/unbiased_dl_baseline.json` line 61)였는데, **뉴스가 전수가 되면서
그 이유가 사라졌다.** 이제 선택편의 없이 뉴스 신호를 재검정할 수 있다.

---

## 1. 지금 바로 쓸 수 있는 것

```powershell
cd C:\Users\3100041\Desktop\sk-square\daily-price-explainer
.\.venv\Scripts\python.exe tools\audit_factor_log.py      # 완전성 감사
.\.venv\Scripts\python.exe -m unittest discover -s tests  # 단위 테스트
```

| 자산 | 위치 | 비고 |
|---|---|---|
| 마스터 시계열 | `data/factor_log.csv` | 1,191행, 상장~2026-10-14 |
| NAV 일별 | `data/nav_daily.csv` | 1,190행 |
| 상장 지분가치 | `data/listed_holdings_daily.csv` | 1,190행 |
| 뉴스 속성 | `data/news_property_codex_batch.jsonl` | 1,399행 |
| 뉴스 검색 로그 | `data/news_search_log_codex.jsonl` | 1,190일, 전수 |
| 피처 생성 | `tools/features.py` | F1~F8 taxonomy 매핑(`FEATURE_TAXONOMY`) |
| 실험 | `tools/experiment_unbiased_dl.py`, `experiment_residual_news.py`, `experiment_f6_f7_news.py`, `experiment_factor_ablation.py` | |
| 기존 결과 | `output/*.json` | 아래 §2 |

---

## 2. 넘어야 할 기준선 (Phase 1, `output/unbiased_dl_baseline.json`)

데이터 `as_of 2026-09-16`, 타깃 `divergence`, n_total 1,173 / n_OOS 704,
expanding window(첫 40% seed, OOS 4블록), 입력 피처 30개.

| 모델 | R² | MAE(%p) | 부호정확도 |
|---|---:|---:|---:|
| mean baseline | −0.005 | 1.927 | 0.472 |
| ridge | −6.79 | 2.946 | 0.706 |
| mlp_2layer | −7.08 | 2.926 | 0.690 |
| **xgboost** | **0.2357** | **1.606** | **0.7259** |

Phase 1 결론(2026-09-17 NAV v2 보정 후, 커밋 `f09ce3e`):
- F1(NAV/하이닉스)·F4(공매도)·F7(매크로)·F8(국면)은 **중립**(leave-one-out 신뢰구간이 0 포함).
  그 이전 "해롭다"는 결론은 **철회됨**.
- **F5(선물)가 새로 유의**, **F3(수급)는 solo·LOO 양쪽 유의** 유지.
- F6·F7 뉴스 분할은 결합 증분 +0.0044로 **미달 → 뉴스 기각**. ← **이게 재검정 대상**

---

## 3. ⚠ 모델링 전에 반드시 알아야 할 데이터 특성 5가지

### 3-1. F4 공매도에 **관행 불연속**이 있다 (가장 중요)

레거시 수집기(`tools/short.py`)가 KRX T+1 공시 지연을 우회하려고 **날짜 D 행에
D-1일의 잔고**를 넣어왔다. 2026-10-01에 확인하고 **"진짜 당일 값"으로 통일하기로
Sean이 결정**했으나, 과거 ~1,180일은 **소급 수정하지 않았다**(비용 대비 실익).

따라서 현재 `shorting_balance`/`_ratio`/`_change`는:
- **2026-09-29 이후**: 진짜 당일 값
- **그 이전 대부분**: 하루 밀린 값
- **예외 6일**(2022-02-04, 2023-10-05, 2024-09-20, 2025-02-03, 2025-10-13,
  2026-02-20): 진짜 당일 값으로 덮어씀

모델이 이 경계를 regime shift로 학습할 수 있다. F4를 쓸 거면 **경계 전후를 나눠
보거나, 과거 구간을 한 칸 shift해 맞추는 것**을 검토할 것. 상세는
`docs/CODEX_HANDOFF_DATA_RESTORE.md` §8의 2026-10-01 Claude Code 항목.

### 3-2. 2026년 10월 NAV는 **잠정값**이다

2026 Q3 실적발표가 **11-16**이라 Q4 거래일의 NAV에 필요한 Q3 비상장가치·순현금이
아직 없다. 그 구간은 직전분기(2026Q2) 값을 잠정 적용하고
**`nav_daily.csv`의 `nav_basis='provisional'`**로 표시해뒀다(현재 10거래일).
`build_nav_daily.py`의 `PROVISIONAL_UNTIL="20261116"`가 기한이며, 넘기면 저장을 보류한다.

**11-16 이후 Q3 값을 넣고 재실행하면 그 10일의 NAV·divergence가 바뀐다.**
즉 지금 그 구간을 포함해 학습하면 나중에 타깃이 달라진다. 재검정을 11-16 이후로
미루거나, 최소한 `nav_basis`로 필터링할 것.

### 3-3. 뉴스는 전수지만 **전부 `review_status: unreviewed`**

사람 검토를 거치지 않은 모델 추출값이다. 또한 과거 행에 **스키마 위반 14건**이 있다
(`primary_subject='market_flow'` 8건, `mechanisms[0]='regulation'` 2건,
`extraction.extractor='claude'` 4건 — 전부 필드 혼동, 값 자체가 틀린 건 아님).
상세는 `data/snapshots/20261015/news_20261014/validation_summary.json`.
Sean이 그대로 두기로 했다.

### 3-4. `*_v1` 컬럼은 **동결 스냅샷**이다

`nav_total_trillion_v1`, `skq_ret_v1`, `divergence_v1` 등은 2026-09-17 NAV v1→v2
마이그레이션 당시 원본을 보존한 것이다. **피처로 쓰지 말 것.** 결측도 정상이다.

### 3-5. 실험 A와 B는 **표본이 다르다**

`unbiased_dl_baseline.json`(A)과 `residual_news_experiment.json`(B)의 수치를
**섞어 인용하지 말 것.** 2026-08판·09-16 백필판 수치도 전부 철회됐다 —
2026-09-17(NAV v2) 이후 수치만 인용한다.

---

## 4. 금지 사항

- ⛔ **`tools/build_event_residual.py` 재실행 금지** — 수기 큐레이션을 덮어쓴다.
- ⛔ 두 뉴스 JSONL은 **append-only**.
- ⛔ `data/*.csv` 직접 편집 금지 — 반드시 스크립트로, **사본 검증 → sha256 전후
  대조 → 원자적 쓰기(tempfile→os.replace)** 순서. 이 저장소의 모든 `repair_*.py`가
  그 패턴을 따르니 그대로 참고할 것.
- ⛔ `.env` 커밋 금지(`KRX_KEY` 포함).
- ⚠ 스케줄러(`scripts/register_scheduler.bat`)는 **아직 미등록**. Q3 반영 전까지 두자.
- ⚠ 커밋 전 `git status`로 의도한 파일만 들어갔는지 확인. `*_local.csv` 백업은
  커밋하지 않는 관례다.

---

## 5. 남은 결측 (모델링에 영향 적음)

| 항목 | 내용 |
|---|---|
| F4 공매도 2일 | 2026-10-13·10-14 — KRX 미공시. 뜨면 `data/manual_krx/shorting_gap_input_20261014_402340.xlsx`에 수기 입력 후 `tools/apply_shorting_manual_input.py` |
| 2021-11-29 | 상장 당일. 구조적 결측(전일 기준값 없음), 뉴스도 미대상 |
| Q3 NAV | 11-16 이후 `UNLISTED_EOK`/`NETCASH_EOK`에 2026Q3 추가 후 재실행 |

F4 공매도는 **pykrx 경로가 KRX에서 차단**돼 자동 수집이 불가능하다(값이 확실히 있는
과거 날짜로 테스트해도 빈 응답). 수기 입력이 유일한 경로다.

---

## 6. 제안하는 첫 작업

1. `python tools/audit_factor_log.py`로 현재 상태 확인(과거 결측 0이어야 함).
2. `tools/features.py`로 피처 생성 후, **§3-1 F4 불연속**을 어떻게 다룰지 먼저 결정.
3. **뉴스 재검정** — Phase 1에서 뉴스를 뺀 이유(선택편의)가 해소됐으므로
   `experiment_residual_news.py`·`experiment_f6_f7_news.py`를 전수 표본으로 재실행.
   기존 기각 결론이 유지되는지 확인하는 것이 이번 단계의 핵심 질문이다.
4. 단, **§3-2 때문에 10월 10일치는 타깃이 바뀔 예정**이다. 11-16 이후 재실행하거나
   그 구간을 제외하고 먼저 돌릴 것.

Sean은 수치를 액면 그대로 받지 않고 교차검증을 요구한다. 결과를 보고할 때는
표본 수·검증 설계·비교 대상을 함께 밝히고, 이전 판 수치와 섞지 말 것.
