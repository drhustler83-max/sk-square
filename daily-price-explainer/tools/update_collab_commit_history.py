"""Refresh the generated commit-history block in COLLAB_BOARD.html.

The block is derived from the real Git log, starting on 2026-07-14.  A
versioned post-commit hook calls this script so the working copy of the board
reflects the commit that was just created.  The generated change is intentionally
left for the next commit: a commit cannot contain its own final hash.
"""

from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
import subprocess


BASE = Path(__file__).resolve().parent.parent
BOARD_PATH = BASE / "COLLAB_BOARD.html"
SINCE = "2026-07-14 00:00:00 +0900"
START_MARKER = "        <!-- COMMIT_HISTORY_START -->"
END_MARKER = "        <!-- COMMIT_HISTORY_END -->"
SECTION_TITLE = "1. \ucd5c\uadfc \ucee4\ubc0b \uc774\ub825"


def _git_history() -> list[tuple[str, str, str]]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(BASE),
            "log",
            f"--since={SINCE}",
            "--date=short",
            "--pretty=format:%h%x1f%ad%x1f%s",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    rows: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        commit_hash, date, subject = line.split("\x1f", 2)
        rows.append((commit_hash, date, subject))
    return rows


def _render(rows: list[tuple[str, str, str]]) -> str:
    output = [START_MARKER, f"        <h4>{SECTION_TITLE}</h4>"]
    for commit_hash, date, subject in rows:
        output.extend(
            [
                "",
                '        <div class="section">',
                (
                    '          <h5><span class="mono">'
                    f"{html.escape(date)} &middot; {html.escape(commit_hash)}"
                    f"</span> &mdash; {html.escape(subject)}</h5>"
                ),
                "        </div>",
            ]
        )
    output.append(END_MARKER)
    return "\n".join(output)


def _replace_block(text: str, rendered: str) -> str:
    if START_MARKER in text or END_MARKER in text:
        if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
            raise RuntimeError("Commit-history markers are missing or duplicated")
        start = text.index(START_MARKER)
        end = text.index(END_MARKER, start) + len(END_MARKER)
        return text[:start] + rendered + text[end:]

    # One-time migration from the artifact-derived static block.
    anchor = text.index("7ab5d45")
    start = text.rfind("        <h4>", 0, anchor)
    end = text.index("        <h4>", anchor)
    if start < 0 or end <= start:
        raise RuntimeError("Could not locate the existing commit-history section")
    return text[:start] + rendered + "\n\n" + text[end:]


def refresh(*, check: bool = False) -> bool:
    original = BOARD_PATH.read_text(encoding="utf-8")
    updated = _replace_block(original, _render(_git_history()))
    changed = updated != original
    if check:
        return changed
    if not changed:
        return False

    temp = BOARD_PATH.with_suffix(".html.commit-history.tmp")
    try:
        temp.write_text(updated, encoding="utf-8", newline="")
        os.replace(temp, BOARD_PATH)
    finally:
        if temp.exists():
            temp.unlink()
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when the generated block is stale; do not write",
    )
    args = parser.parse_args()
    changed = refresh(check=args.check)
    if args.check:
        raise SystemExit(1 if changed else 0)
    print(f"commit_history_updated={str(changed).lower()}")


if __name__ == "__main__":
    main()
