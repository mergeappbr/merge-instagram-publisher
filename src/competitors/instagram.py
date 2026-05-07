"""
Coleta IG via Meta Graph Business Discovery API.

Endpoint: GET /{IG_BUSINESS_ACCOUNT_ID}
  ?fields=business_discovery.username({handle}){followers_count,media_count,
          media.limit(25){id,caption,like_count,comments_count,media_type,
                          timestamp,permalink}}
  &access_token=META_GRAPH_ACCESS_TOKEN

Restrições documentadas pela Meta:
- O target deve ser conta IG Business OU Creator (não Personal).
- Limite ~ 25 últimos media items.
- Quota global compartilhada com outras chamadas Graph (200/h por user).

Erros são capturados e o handle é pulado — nunca derruba o digest inteiro.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

GRAPH_BASE = "https://graph.facebook.com/v21.0"
TIMEOUT = 20.0
MEDIA_LIMIT = 25


def _ig_account_id() -> str:
    return os.getenv("IG_BUSINESS_ACCOUNT_ID", "").strip()


def _token() -> str:
    return os.getenv("META_GRAPH_ACCESS_TOKEN", "").strip()


def fetch_handle(handle: str) -> dict | None:
    """Retorna dict com followers_count, media_count, media[]; None se falhar."""
    ig_id = _ig_account_id()
    token = _token()
    if not ig_id or not token:
        print("⚠ competitors/ig · IG_BUSINESS_ACCOUNT_ID ou META_GRAPH_ACCESS_TOKEN ausente")
        return None
    if not handle:
        return None

    fields = (
        f"business_discovery.username({handle})"
        "{followers_count,media_count,"
        f"media.limit({MEDIA_LIMIT})"
        "{id,caption,like_count,comments_count,media_type,timestamp,permalink}}"
    )
    url = f"{GRAPH_BASE}/{ig_id}"
    try:
        r = httpx.get(
            url,
            params={"fields": fields, "access_token": token},
            timeout=TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ competitors/ig · {handle} fetch erro: {e!r}")
        return None
    if r.status_code != 200:
        print(f"⚠ competitors/ig · {handle} HTTP {r.status_code}: {r.text[:200]}")
        return None
    try:
        data = r.json()
    except ValueError:
        print(f"⚠ competitors/ig · {handle} resposta não é JSON")
        return None
    bd = data.get("business_discovery")
    if not bd:
        print(f"⚠ competitors/ig · {handle} sem business_discovery (perfil personal ou inexistente?)")
        return None
    return bd


def filter_recent(media: list[dict], days: int = 7) -> list[dict]:
    """Filtra media com timestamp dentro dos últimos `days`."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    for m in media or []:
        ts = m.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if dt >= cutoff:
            out.append(m)
    return out


def avg_engagement(media: list[dict]) -> dict:
    """Média (likes+comments) por post na lista. Retorna {avg_engagement, posts}."""
    if not media:
        return {"avg_engagement": 0.0, "avg_likes": 0.0, "avg_comments": 0.0, "posts": 0}
    likes = [int(m.get("like_count") or 0) for m in media]
    comments = [int(m.get("comments_count") or 0) for m in media]
    n = len(media)
    return {
        "avg_engagement": (sum(likes) + sum(comments)) / n,
        "avg_likes": sum(likes) / n,
        "avg_comments": sum(comments) / n,
        "posts": n,
    }
