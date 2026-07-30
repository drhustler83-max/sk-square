# DPE Unbiased News Search Protocol v1

## 목적

`event_residual_unbiased.csv`의 high-residual case와 matched low-residual
control에 동일한 검색 절차를 적용한다. 검색·Property 추출 단계에서는 주가,
divergence, high/low 역할, 수기 event_name을 작업자와 LLM에 노출하지 않는다.

## 대상 거래일

- Case: `abs(divergence) > mean(abs(divergence))`
- 2026-07-27 기준 임계값: `1.7610456942%p`(표시값 `1.76%p`)
- Case 수: 438일
- Control: 같은 연도에서 KOSPI 20일 방향·변동성 수준이 가장 가까운 1:1
  unique match 438일
- 검색 대상 합계: 876일

## 뉴스 유효시간

종가-대-종가 잔차를 설명하기 위해 거래일 D의 검색 창은 다음과 같다.

- 시작: 직전 거래일 15:30 KST 초과
- 종료: 거래일 D 15:30 KST 이하
- D 15:30 이후 공개된 기사는 다음 거래일 검색 창에 포함
- 주말·휴일 공개 기사는 다음 거래일에 포함
- 게시시각을 확인할 수 없으면 `event_time_bucket=unknown`

## 고정 검색어

모든 case와 control 날짜에 아래 쿼리를 동일하게 적용한다.

1. 직접 언급
   - `"SK스퀘어"`
   - `"SK Square"`
2. 핵심 자회사·포트폴리오와 SK스퀘어 동시 언급
   - `("11번가" OR "원스토어" OR "SK쉴더스" OR "티맵모빌리티" OR
     "콘텐츠웨이브" OR "드림어스컴퍼니") ("SK스퀘어" OR "SK Square")`
3. 자본배분·지배구조
   - `"SK스퀘어" (자사주 OR 소각 OR 주주환원 OR 밸류업 OR NAV OR 할인율
     OR IPO OR 매각 OR 투자 OR 합병 OR 상법 OR 지배구조)`
4. 하이닉스 연계 이슈
   - `"SK하이닉스" "SK스퀘어"`

검색엔진의 날짜 필터는 뉴스 유효시간보다 넓은 달력일 범위로 조회한 뒤,
최종 포함 여부는 실제 게시시각으로 판정한다.

## 포함·제외 규칙

### 포함

- 언론사 기사
- 통신사 기사
- 증권사·투자정보 매체 기사
- SK스퀘어 또는 포트폴리오 가치·할인율·수급에 연결 가능한 기사

### 제외

- DART 원문
- SK스퀘어·SK그룹 공식 홈페이지 원문
- 블로그·카페·게시판·SNS
- URL 또는 게시시각을 확인할 수 없는 검색 스니펫

공식 공시와 홈페이지 자료는 별도 Evidence source로 유지하되 News Property
모델의 입력에서는 제외한다.

## 후보 보존·중복 처리

- 검색 결과를 residual과 비교해 삭제하지 않는다.
- 관련성이 낮아도 `relevance_score`를 낮게 주고 원본 후보를 보존한다.
- 동일 통신사 문구를 재전송한 기사는 title/text hash로 cluster 처리한다.
- cluster별 최초 공개 기사 1건을 대표로 사용한다.
- 날짜별 최대 기사 수를 강제로 자르기 전에 direct relevance 순으로 정렬한다.
- 검색 결과 없음과 검색 실패를 구분한다.
  - `completed_no_news`
  - `completed_with_articles`
  - `retrieval_failed`

## Property 추출 역할

1. Codex 또는 Claude Code가 기사 본문만 보고
   `schemas/news_property.schema.json`의 Property를 1차 추출한다.
2. 다른 모델이 최초 50건과 이후 무작위 10%를 독립적으로 재채점한다.
3. 범주형 불일치 또는 연속 점수 차이 `>0.30`, confidence `<0.60`,
   materiality `>=0.80`인 기사만 사람이 검토한다.
4. 사람의 검토자는 기사 내용만 보고 판단한다. 가격·잔차는 보지 않는다.
5. 모델명, prompt version, 추출시각, 사람 수정 필드를 모두 기록한다.

## 모델 평가

- 숫자 팩터만
- 뉴스 Property만
- 숫자 팩터 + 뉴스 Property

위 세 모델을 동일한 expanding-window OOS 구간에서 비교한다. 뉴스의 증분
효과는 결합 모델과 숫자 팩터 모델의 `ΔOOS R²`, `ΔMAE`, `ΔPR-AUC`로 평가한다.
