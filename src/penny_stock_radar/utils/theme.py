from __future__ import annotations

from collections import Counter

THEME_KEYWORDS = {
    "ai": ("artificial intelligence", "ai ", "machine learning", "llm", "inference"),
    "crypto": ("bitcoin", "crypto", "blockchain", "digital asset", "token"),
    "biotech": ("clinical", "fda", "drug", "pharma", "biotech", "therapeutic"),
    "energy": ("battery", "solar", "nuclear", "uranium", "energy", "oil", "gas"),
    "defense": ("defense", "military", "drone", "missile", "aerospace"),
    "ev": ("electric vehicle", "ev ", "charging", "battery", "mobility"),
    "mining": ("gold", "silver", "lithium", "rare earth", "mining", "copper"),
}


def extract_themes(*texts: str | None) -> list[str]:
    combined = " ".join(text or "" for text in texts).lower()
    matches: list[str] = []
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword in combined for keyword in keywords):
            matches.append(theme)
    return matches


def dominant_themes(theme_lists: list[list[str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for themes in theme_lists:
        counter.update(themes)
    return counter
