import sys, os; sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv; load_dotenv()
from tools.futures import get_futures_data as g
for d in ["20260616", "20250603", "20240603", "20230602", "20220603", "20211206"]:
    r = g("402340", d)
    if r.get("listed"):
        nm = r.get("near_month", {})
        print(d, "LISTED basis=", r.get("basis"), "vol=", nm.get("volume"), nm.get("futures_ticker"))
    else:
        print(d, "NOT LISTED:", r.get("error"))
