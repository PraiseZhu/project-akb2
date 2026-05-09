"""Atomic paired-write: both temp files succeed before either rename.

If anything fails midway, every temp file and any rename we already
performed is undone, leaving zero orphan official outputs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PairedWriteError(IOError):
    pass


@dataclass(frozen=True)
class WrittenPair:
    out_path: Path
    report_path: Path


def write_output_pair(*, jsonl_text: str, report_text: str, out_path: Path, report_path: Path) -> WrittenPair:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    out_tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    report_tmp = report_path.with_suffix(report_path.suffix + ".tmp")

    out_renamed = False
    report_renamed = False
    try:
        out_tmp.write_text(jsonl_text, encoding="utf-8")
        report_tmp.write_text(report_text, encoding="utf-8")

        os.replace(out_tmp, out_path)
        out_renamed = True
        os.replace(report_tmp, report_path)
        report_renamed = True

        return WrittenPair(out_path=out_path, report_path=report_path)
    except OSError as exc:
        # Roll back any partial state so we never leave orphan official files.
        if out_renamed and not report_renamed:
            try:
                out_path.unlink()
            except OSError:
                pass
        for tmp in (out_tmp, report_tmp):
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        raise PairedWriteError(f"paired write failed: {exc}") from exc
