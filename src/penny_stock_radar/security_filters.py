from __future__ import annotations

import re


def listing_filter_reasons(
    *,
    symbol: str,
    company_name: str,
    exclude_units: bool = True,
    exclude_preferred: bool,
    exclude_warrants: bool,
    exclude_rights: bool,
    exclude_spacs: bool,
) -> list[str]:
    normalized_symbol = str(symbol or "").strip().upper()
    security_name = str(company_name or "").strip().lower()
    reasons: list[str] = []

    if (
        not normalized_symbol
        or normalized_symbol.startswith("$")
        or re.search(r"[^A-Z0-9.\-]", normalized_symbol)
    ):
        reasons.append("invalid_symbol_format")
    if exclude_units and _is_unit_security(normalized_symbol, security_name):
        reasons.append("unit_security")
    if exclude_preferred and _is_preferred_security(normalized_symbol, security_name):
        reasons.append("preferred_security")
    if exclude_warrants and _is_warrant_security(normalized_symbol, security_name):
        reasons.append("warrant_security")
    if exclude_rights and _is_rights_security(normalized_symbol, security_name):
        reasons.append("rights_security")
    if exclude_spacs and _is_spac_security(security_name):
        reasons.append("spac_security")
    return reasons


def _is_preferred_security(symbol: str, security_name: str) -> bool:
    if "preferred" in security_name or "preference" in security_name:
        return True
    return bool(re.search(r"(?:\$|[.\-](?:P|PR|PRA|PRB|PRC|PRD|PRE|PF))", symbol))


def _is_unit_security(symbol: str, security_name: str) -> bool:
    if any(keyword in security_name for keyword in (" unit", " units", "unit ", "units ", "유닛")):
        return True
    return bool(re.search(r"(?:[.\-/])(U|UN|UNT)$", symbol))


def _is_warrant_security(symbol: str, security_name: str) -> bool:
    if any(keyword in security_name for keyword in ("warrant", "warrants")):
        return True
    return bool(re.search(r"(?:[.\-/])(W|WS|WT|WTS)$", symbol))


def _is_rights_security(symbol: str, security_name: str) -> bool:
    if any(keyword in security_name for keyword in (" right", " rights", "권리")):
        return True
    return bool(re.search(r"(?:[.\-/])(R|RT|RTS)$", symbol))


def _is_spac_security(security_name: str) -> bool:
    return any(
        keyword in security_name
        for keyword in (
            "acquisition corp",
            "acquisition corporation",
            "acquisition company",
            "special purpose acquisition",
            "blank check",
        )
    )
