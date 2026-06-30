"""
Daily Price Move Explainer
엔트리포인트 — CLI 또는 스케줄러에서 직접 실행
"""
import sys
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

load_dotenv()


class _PykrxFilter:
    """pykrx 내부 'Error occurred in ...' print 억제"""
    _SUPPRESS = ("Error occurred in",)

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        if not any(text.startswith(s) for s in self._SUPPRESS):
            self._stream.write(text)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, attr):
        return getattr(self._stream, attr)


sys.stdout = _PykrxFilter(sys.stdout)


def run_daily_briefing(date: str = None):
    """장 마감 후 자동 브리핑 생성"""
    from agents.orchestrator import chat
    date = date or datetime.today().strftime("%Y%m%d")
    logger.info(f"=== Daily Briefing: {date} ===")
    result = chat("오늘 주가 브리핑 요약해줘", date=date)
    print(result)
    return result


def run_factor_log(date: str = None):
    """장 마감 후 팩터 데이터를 data/factor_log.csv에 저장"""
    from tools.factor_logger import collect_and_log, date_count
    date = date or datetime.today().strftime("%Y%m%d")
    logger.info(f"=== Factor Log: {date} ===")
    row = collect_and_log(date)
    days = date_count()
    print(f"\n[{date}] 저장 완료 — 누적 {days}거래일")
    if days < 60:
        print(f"  → OLS 베타 산출까지 {60 - days}거래일 남음")
    else:
        print(f"  → 베타 산출 가능: python main.py beta")
    return row


def run_chat():
    """대화형 CLI 모드"""
    from agents.orchestrator import chat
    print("Daily Price Move Explainer (종료: 'q')")
    print("-" * 40)

    while True:
        query = input("\n질문: ").strip()
        if query.lower() in ("q", "quit", "exit"):
            break
        if not query:
            continue
        try:
            response = chat(query)
            print(f"\n{response}")
        except Exception as e:
            logger.error(f"Error: {e}")
            print(f"오류 발생: {e}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "chat"

    if mode == "briefing":
        date = sys.argv[2] if len(sys.argv) > 2 else None
        run_daily_briefing(date)
    elif mode == "log":
        date = sys.argv[2] if len(sys.argv) > 2 else None
        run_factor_log(date)
    elif mode == "beta":
        from tools.beta import print_betas
        print_betas()
    elif mode == "features":
        from tools.features import feature_summary
        fargs = [a for a in sys.argv[2:] if not a.startswith("--")]
        lookback = int(fargs[0]) if fargs else None
        feature_summary(lookback=lookback)
    elif mode == "events":
        from tools.events import residual_event_analysis
        residual_event_analysis()
    elif mode == "backfill-futures":
        from tools.backfill_futures import backfill_futures
        backfill_futures()
    elif mode == "backfill":
        from tools.backfill import backfill, DEFAULT_START
        bf_args = sys.argv[2:]
        rebuild = "--rebuild" in bf_args
        positional = [a for a in bf_args if not a.startswith("--")]
        start = positional[0] if len(positional) > 0 else DEFAULT_START
        end = positional[1] if len(positional) > 1 else None
        backfill(start=start, end=end, rebuild=rebuild)
    elif mode == "attribution":
        from tools.attribution import print_attribution
        date = sys.argv[2] if len(sys.argv) > 2 else None
        print_attribution(date)
    else:
        run_chat()
