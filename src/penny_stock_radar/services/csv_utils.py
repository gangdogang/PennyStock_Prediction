from __future__ import annotations

from collections.abc import Iterable, Iterator


def iter_non_comment_lines(lines: Iterable[str]) -> Iterator[str]:
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        yield line
