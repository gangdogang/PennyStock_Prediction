from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

import httpx

from ..config import AppSettings
from ..kis_auth import load_cached_token, store_cached_token


@dataclass(frozen=True, slots=True)
class KisApiResponse:
    payload: dict[str, Any]
    headers: dict[str, str]


class KisApiClient:
    def __init__(
        self,
        settings: AppSettings,
        *,
        base_url: str,
        app_key: str | None,
        app_secret: str | None,
        token_cache_name: str,
        environment: str = "prod",
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.environment = environment.strip().lower()
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=settings.live_market_timeout_seconds,
        )
        self._owns_client = client is None
        self._token_cache_path = settings.cache_dir / token_cache_name
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def get(
        self,
        path: str,
        *,
        tr_id: str,
        params: dict[str, Any] | None = None,
        tr_cont: str = "",
    ) -> KisApiResponse:
        return self._request_json(
            "GET",
            path,
            tr_id=tr_id,
            params=params,
            tr_cont=tr_cont,
        )

    def post(
        self,
        path: str,
        *,
        tr_id: str,
        body: dict[str, Any],
        tr_cont: str = "",
        include_hashkey: bool = False,
    ) -> KisApiResponse:
        return self._request_json(
            "POST",
            path,
            tr_id=tr_id,
            body=body,
            tr_cont=tr_cont,
            include_hashkey=include_hashkey,
        )

    def issue_hashkey(self, payload: dict[str, Any]) -> str | None:
        if not self.is_configured():
            return None
        headers = {
            "content-type": "application/json",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
        }
        try:
            response = self._client.post("/uapi/hashkey", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        hashkey = data.get("HASH")
        return str(hashkey) if hashkey else None

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        tr_id: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        tr_cont: str = "",
        include_hashkey: bool = False,
    ) -> KisApiResponse:
        token = self._ensure_token()
        if not token:
            raise RuntimeError("KIS credentials are missing or token issuance failed.")
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": self._normalize_tr_id(tr_id),
            "custtype": self.settings.kis_custtype,
            "tr_cont": tr_cont,
            "content-type": "application/json",
        }
        if method == "POST" and include_hashkey and body is not None:
            hashkey = self.issue_hashkey(body)
            if hashkey:
                headers["hashkey"] = hashkey
        for attempt in range(3):
            try:
                if method == "POST":
                    response = self._client.post(path, json=body, headers=headers)
                else:
                    response = self._client.get(path, params=params, headers=headers)
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(f"KIS request failed for {path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"KIS returned a non-dict payload for {path}.")
            if str(payload.get("msg_cd")) == "EGW00201" and attempt < 2:
                time.sleep(0.35 * (attempt + 1))
                continue
            if response.is_success and str(payload.get("rt_cd", "0")) in {"0", ""}:
                return KisApiResponse(
                    payload=payload,
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
            msg_cd = payload.get("msg_cd") or response.status_code
            msg1 = payload.get("msg1") or response.text
            raise RuntimeError(f"KIS request failed for {path}: {msg_cd} {msg1}")
        raise RuntimeError(f"KIS request retry budget exhausted for {path}.")

    def _ensure_token(self) -> str | None:
        if self._access_token and self._token_expires_at and self._token_expires_at > self._utc_now():
            return self._access_token
        cached_token, cached_expires_at = load_cached_token(self._token_cache_path)
        if cached_token and cached_expires_at:
            self._access_token = cached_token
            self._token_expires_at = cached_expires_at
            return self._access_token
        if not self.app_key or not self.app_secret:
            return None
        try:
            response = self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                headers={"content-type": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("access_token")
        if not token:
            return None
        self._access_token = str(token)
        self._token_expires_at = self._parse_utc_timestamp(
            payload.get("access_token_token_expired")
            or payload.get("access_token_expired")
        ) or self._utc_now()
        try:
            store_cached_token(
                self._token_cache_path,
                access_token=self._access_token,
                expires_at=self._token_expires_at,
            )
        except OSError:
            pass
        return self._access_token

    def _normalize_tr_id(self, tr_id: str) -> str:
        normalized = str(tr_id or "").strip().upper()
        if self.environment != "mock":
            return normalized
        if normalized.startswith(("T", "J", "C")) and not normalized.startswith("V"):
            return f"V{normalized[1:]}"
        return normalized

    def _parse_utc_timestamp(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)
