from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

for path in (SRC, ROOT):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


def load_first_module(candidates: Iterable[str]):
    errors: list[str] = []
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            errors.append(f"{module_name}: {exc}")
    raise AssertionError(
        "Could not import any candidate module. "
        "Expected the Milestone 1 implementation to provide one of: "
        f"{', '.join(candidates)}. "
        f"Import errors: {' | '.join(errors)}"
    )


def normalize_records(result):
    if result is None:
        return []

    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict(orient="records")
            if isinstance(records, list):
                return records
        except TypeError:
            pass

    if isinstance(result, dict):
        return [result]

    return list(result)
