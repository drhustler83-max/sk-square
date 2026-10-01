"""Retired: the old backfill wrote won amounts into share-quantity F3 fields.

Use tools/import_investor_quantity.py for historical F3 quantities. Ownership
ratios for the preserved recent dates are loaded by that importer as well.
"""


def backfill_extra(*_args, **_kwargs):
    raise RuntimeError(
        "거래대금(원) 개인 수급 백필은 폐기됐습니다. "
        "수량(주) 적재는 tools/import_investor_quantity.py를 사용하세요."
    )


if __name__ == "__main__":
    backfill_extra()
