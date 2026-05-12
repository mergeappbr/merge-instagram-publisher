"""Pool inteligente de fotos REAIS no R2 — semantic matching, NÃO sortição.

Pipeline:
  1. Manifest local (`brand/images/_diogo_index.json`) tem 892 fotos taggeadas
     por Vision (sport, scene, gear_visible, framing, face_visible, mood,
     themes, usable_for_news, quality_score).
  2. Pool R2 (`r2://merge-creatives/photo-pool/<sha>.jpg`) tem 150 fotos
     selecionadas com diversidade temática e quality≥7. Estado em
     `output/.r2_photo_pool.json`.
  3. lookup_for_news(title, summary, modality) → filtra pool por relevância
     semântica (overlap de temas/sport/cenas), penaliza recém-usadas, retorna
     URL pública R2 da melhor candidata.
  4. mark_used(sha) → deleta do R2, marca `last_used_at`, status=used. Foto
     volta a ser elegível 45d depois.
  5. refill_if_needed() → se pool available < 50, sobe próximas N (até 150)
     respeitando cooldown e diversidade.

Filosofia: NUNCA sortear. Sempre escolher por mérito (qualidade + relevância
+ diversidade). Roda no scheduler (cron diário).
"""
from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = ROOT / "brand" / "images" / "_diogo_index.json"
POOL_STATE = ROOT / "output" / ".r2_photo_pool.json"
R2_PREFIX = "photo-pool"

POOL_TARGET_SIZE = 150
POOL_REFILL_THRESHOLD = 50
COOLDOWN_DAYS = 45
QUALITY_MIN = 7

# Distribuição-alvo por tema (running é core). Soma = 150.
# Faz match contra `sport` + `scene` do manifest.
THEME_QUOTAS = {
    "running":      50,  # qualquer running (road/track/trail) — CORE
    "swimming":     25,
    "cycling":      25,
    "gear_close":   20,  # scene=gear_close_up OU framing=close_up + gear_visible
    "finish_line":  15,  # scene in {finish_line, podium, start_line, transition}
    "landscape":    15,  # scene=landscape OU framing=wide + people_count<=1
}


# ─── Manifest I/O ─────────────────────────────────────────────────────

def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"version": 1, "photos": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "photos": {}}


def load_pool_state() -> dict:
    if not POOL_STATE.exists():
        return {"version": 1, "entries": {}}
    try:
        return json.loads(POOL_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}


def save_pool_state(state: dict) -> None:
    POOL_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POOL_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POOL_STATE)


# ─── Categorização (foto → tema-bucket) ───────────────────────────────

def _photo_themes(meta: dict) -> set[str]:
    """Devolve buckets temáticos que a foto satisfaz (1 foto pode estar em N)."""
    sports = set(meta.get("sport") or [])
    scene = meta.get("scene", "")
    framing = meta.get("framing", "")
    gear = meta.get("gear_visible") or []
    ppl = int(meta.get("people_count") or 0)
    buckets: set[str] = set()
    if sports & {"running", "trail"}:
        buckets.add("running")
    if "swimming" in sports or scene in ("pool", "open_water"):
        buckets.add("swimming")
    if "cycling" in sports or "triathlon" in sports and scene in ("road", "transition"):
        if "cycling" in sports:
            buckets.add("cycling")
    if scene == "gear_close_up" or (framing == "close_up" and gear):
        buckets.add("gear_close")
    if scene in ("finish_line", "podium", "start_line", "transition"):
        buckets.add("finish_line")
    if scene == "landscape" or (framing == "wide" and ppl <= 1):
        buckets.add("landscape")
    return buckets


# ─── Seleção top-N (diversidade + qualidade) ──────────────────────────

def _eligible_photos(manifest: dict, used_recently_shas: set[str]) -> list[tuple[str, dict]]:
    """Filtra fotos elegíveis: usable_for_news + quality≥QUALITY_MIN +
    fora de cooldown."""
    out: list[tuple[str, dict]] = []
    for sha, meta in manifest.get("photos", {}).items():
        if not meta.get("usable_for_news"):
            continue
        if int(meta.get("quality_score") or 0) < QUALITY_MIN:
            continue
        if sha in used_recently_shas:
            continue
        out.append((sha, meta))
    return out


def select_top_n_by_theme(manifest: dict, used_recently_shas: set[str],
                          target: int = POOL_TARGET_SIZE) -> list[tuple[str, dict, str]]:
    """Escolhe top-N respeitando quotas por tema. Devolve [(sha, meta, theme), ...].
    Para cada tema: ordena por quality_score desc + people_count asc (preferir
    sem rosto) e pega quota. Sobras vão pra wildcards.
    """
    eligible = _eligible_photos(manifest, used_recently_shas)
    # Indexa por tema
    by_theme: dict[str, list[tuple[str, dict]]] = {t: [] for t in THEME_QUOTAS}
    for sha, meta in eligible:
        for t in _photo_themes(meta):
            if t in by_theme:
                by_theme[t].append((sha, meta))

    chosen: dict[str, str] = {}  # sha → theme atribuído
    for theme, quota in THEME_QUOTAS.items():
        cands = by_theme[theme]
        # Quality desc, depois preferir face_visible=False, depois quality_score asc tiebreak
        cands.sort(key=lambda x: (
            -int(x[1].get("quality_score") or 0),
            int(x[1].get("face_visible") and 1 or 0),
            int(x[1].get("people_count") or 0),
        ))
        for sha, meta in cands:
            if sha in chosen:
                continue
            chosen[sha] = theme
            if sum(1 for t in chosen.values() if t == theme) >= quota:
                break

    # Devolve na ordem (sha, meta, theme)
    photos = manifest.get("photos", {})
    return [(sha, photos[sha], theme) for sha, theme in chosen.items() if sha in photos][:target]


# ─── R2 client ────────────────────────────────────────────────────────

def _r2_client():
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
    )


def _r2_key(sha: str, ext: str) -> str:
    return f"{R2_PREFIX}/{sha}{ext}"


def _r2_url(sha: str, ext: str) -> str:
    base = (os.environ.get("R2_PUBLIC_BASE_URL")
            or os.environ.get("R2_PUBLIC_BASE", "")).rstrip("/")
    return f"{base}/{_r2_key(sha, ext)}"


def _ext_for(meta: dict) -> str:
    fn = meta.get("filename", "")
    suf = Path(fn).suffix.lower()
    return suf if suf in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"


def _upload_one(sha: str, meta: dict, theme: str) -> Optional[dict]:
    src = Path(meta["path"])
    if not src.exists():
        print(f"⚠ pool.upload: src sumiu {src}")
        return None
    ext = _ext_for(meta)
    key = _r2_key(sha, ext)
    try:
        client = _r2_client()
        ctype = "image/png" if ext == ".png" else (
            "image/webp" if ext == ".webp" else "image/jpeg")
        client.upload_file(
            str(src),
            os.environ["R2_BUCKET"],
            key,
            ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=31536000"},
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ pool.upload falhou ({sha[:8]}): {e!r}")
        return None
    return {
        "sha": sha,
        "url": _r2_url(sha, ext),
        "key": key,
        "theme": theme,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "last_used_at": None,
        "status": "available",
        # Snapshot de tags pra evitar reler manifest no lookup
        "tags": {
            "sport": meta.get("sport") or [],
            "scene": meta.get("scene", ""),
            "gear_visible": meta.get("gear_visible") or [],
            "framing": meta.get("framing", ""),
            "face_visible": bool(meta.get("face_visible")),
            "people_count": int(meta.get("people_count") or 0),
            "mood": meta.get("mood", ""),
            "themes": meta.get("themes") or [],
            "quality_score": int(meta.get("quality_score") or 0),
            "notes": meta.get("notes", ""),
        },
    }


def _delete_one(entry: dict) -> bool:
    try:
        _r2_client().delete_object(Bucket=os.environ["R2_BUCKET"], Key=entry["key"])
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠ pool.delete falhou ({entry.get('sha','?')[:8]}): {e!r}")
        return False


# ─── Cooldown ─────────────────────────────────────────────────────────

def _shas_in_cooldown(state: dict, now: Optional[datetime] = None) -> set[str]:
    """SHAs cujo last_used_at < COOLDOWN_DAYS atrás."""
    now = now or datetime.now()
    cutoff = now - timedelta(days=COOLDOWN_DAYS)
    out: set[str] = set()
    for sha, e in state.get("entries", {}).items():
        ts = e.get("last_used_at")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if t > cutoff:
            out.add(sha)
    return out


# ─── Refill ───────────────────────────────────────────────────────────

def available_count(state: Optional[dict] = None) -> int:
    s = state or load_pool_state()
    return sum(1 for e in s.get("entries", {}).values()
               if e.get("status") == "available")


def refill_if_needed(force: bool = False) -> dict:
    """Se pool available < threshold, sobe pras melhores fotos disponíveis até
    target. Respeita cooldown 45d. Devolve {uploaded, available_after, ...}.
    """
    state = load_pool_state()
    avail = available_count(state)
    if avail >= POOL_REFILL_THRESHOLD and not force:
        return {"action": "skip", "available": avail, "uploaded": 0}

    manifest = load_manifest()
    if not manifest.get("photos"):
        return {"action": "no_manifest", "available": avail, "uploaded": 0}

    # SHAs já no R2 (qualquer status) + em cooldown não devem ser re-uploaded
    in_pool = set(state.get("entries", {}).keys())
    in_cooldown = _shas_in_cooldown(state)
    skip_shas = in_pool | in_cooldown

    # Quanto sobe? Restaura até target, descontando o que já está available.
    needed = POOL_TARGET_SIZE - avail
    if needed <= 0:
        return {"action": "full", "available": avail, "uploaded": 0}

    # Re-elegíveis (cooldown vencido) que JÁ estão no state mas com status=used —
    # marcamos de volta como available SEM reupload (objeto foi deletado, mas
    # se manifest path ainda existe, podemos re-subir como new entry pra ter URL).
    # Estratégia mais simples: deleta entries com status=used e cooldown vencido,
    # e re-upload via select.
    entries = state.setdefault("entries", {})
    cutoff = datetime.now() - timedelta(days=COOLDOWN_DAYS)
    expired_used: list[str] = []
    for sha, e in list(entries.items()):
        if e.get("status") == "used":
            ts = e.get("last_used_at")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if t <= cutoff:
                expired_used.append(sha)
    for sha in expired_used:
        del entries[sha]
    # Atualiza skip set após limpar expirados
    in_pool = set(entries.keys())
    skip_shas = in_pool | _shas_in_cooldown(state)

    candidates = select_top_n_by_theme(manifest, skip_shas, target=needed)
    uploaded = 0
    for sha, meta, theme in candidates:
        e = _upload_one(sha, meta, theme)
        if e:
            entries[sha] = e
            uploaded += 1

    state["last_refill_at"] = datetime.now().isoformat(timespec="seconds")
    save_pool_state(state)
    return {
        "action": "refilled",
        "available": available_count(state),
        "uploaded": uploaded,
        "expired_used_recycled": len(expired_used),
    }


# ─── Lookup semântico ─────────────────────────────────────────────────

_STOP_PT = {
    "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "para", "por", "com", "sem", "que", "se", "um", "uma", "uns", "umas",
    "o", "a", "os", "as", "ao", "à", "aos", "às", "como", "mais", "menos",
    "ja", "já", "foi", "ser", "ter", "vai", "ele", "ela", "este", "essa",
    "isso", "muito", "muita", "pode", "tudo", "todo", "toda", "the", "a",
    "an", "of", "to", "in", "on", "for", "with", "and", "or", "is", "was",
}

_KEYWORD_TO_TAGS = {
    # palavras → conjunto de tags que devem aparecer no scene/themes/gear
    "natacao": {"swimming", "pool", "open_water"},
    "natação": {"swimming", "pool", "open_water"},
    "swim": {"swimming", "pool", "open_water"},
    "piscina": {"pool"},
    "agua": {"open_water", "pool"},
    "água": {"open_water", "pool"},
    "mar": {"open_water"},
    "corrida": {"running", "road", "track", "trail"},
    "correr": {"running", "road"},
    "maratona": {"running", "finish_line", "road"},
    "marathon": {"running", "finish_line", "road"},
    "trail": {"trail", "running"},
    "ultra": {"trail", "running"},
    "track": {"track", "running"},
    "pista": {"track", "running"},
    "ciclismo": {"cycling", "road"},
    "bike": {"cycling", "bike"},
    "bicicleta": {"cycling", "bike"},
    "cyclist": {"cycling", "bike"},
    "cycling": {"cycling", "bike"},
    "triatlo": {"triathlon", "swimming", "cycling", "running"},
    "triathlon": {"triathlon", "swimming", "cycling", "running"},
    "ironman": {"triathlon", "running", "cycling", "swimming"},
    "70.3": {"triathlon"},
    "tenis": {"running_shoes", "gear_close_up"},
    "tênis": {"running_shoes", "gear_close_up"},
    "shoe": {"running_shoes", "gear_close_up"},
    "shoes": {"running_shoes", "gear_close_up"},
    "relogio": {"watch", "gear_close_up"},
    "relógio": {"watch", "gear_close_up"},
    "watch": {"watch", "gear_close_up"},
    "garmin": {"watch", "gear_close_up"},
    "wearable": {"watch", "gear_close_up"},
    "smartwatch": {"watch", "gear_close_up"},
    "chegada": {"finish_line"},
    "linha": {"finish_line", "start_line"},
    "podio": {"podium"},
    "pódio": {"podium"},
    "vitoria": {"podium", "victory"},
    "vitória": {"podium", "victory"},
    "treino": {"training"},
    "training": {"training"},
    "competição": {"competition"},
    "competition": {"competition"},
    "lesão": {"running"},  # injury news → running scene
    "lesao": {"running"},
    "injury": {"running"},
    "fadiga": {"running"},
    "cafeina": {"running", "training"},
    "cafeína": {"running", "training"},
    "z2": {"running", "cycling", "training"},
    "zona 2": {"running", "cycling", "training"},
    "vo2": {"running", "track", "training"},
}


def _normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _extract_keywords(text: str) -> set[str]:
    """Tokeniza title+summary, devolve set de tokens relevantes (sem stop words)."""
    norm = _normalize_text(text)
    tokens = set(re.findall(r"[a-z0-9]{3,}", norm))
    return tokens - _STOP_PT


def _expand_to_tags(tokens: set[str]) -> set[str]:
    """Mapeia tokens de notícia → tags semânticas."""
    out: set[str] = set()
    for tok in tokens:
        if tok in _KEYWORD_TO_TAGS:
            out |= _KEYWORD_TO_TAGS[tok]
    return out


def _score_entry(entry: dict, query_tags: set[str], query_modality: str,
                 now: Optional[datetime] = None) -> float:
    """Score de relevância da entry pra query.
    Componentes:
      + match de sport com modality (até +5)
      + overlap de scene/gear/themes com query_tags (cada tag +2)
      + qualidade (quality_score, 0-10) escalado x0.5
      + bonus -1 se face_visible (preferimos sem rosto)
      + recência: penaliza se uploaded_at < 7 dias (já apareceu recente)
    """
    tags = entry.get("tags") or {}
    score = 0.0

    # Sport vs modality
    sports = set(tags.get("sport") or [])
    mod = (query_modality or "").lower()
    mod_map = {
        "running": {"running", "trail"},
        "swim": {"swimming"},
        "swimming": {"swimming"},
        "cycling": {"cycling", "triathlon"},
        "bike": {"cycling", "triathlon"},
        "triathlon": {"triathlon", "swimming", "cycling", "running"},
        "trail": {"trail", "running"},
        "wellness": set(),
    }
    expected = mod_map.get(mod, set())
    if sports & expected:
        score += 5.0

    # Overlap query_tags x (scene + gear + themes + sport)
    photo_tags: set[str] = set()
    photo_tags |= sports
    if tags.get("scene"):
        photo_tags.add(tags["scene"])
    photo_tags |= set(tags.get("gear_visible") or [])
    photo_tags |= set(tags.get("themes") or [])
    overlap = query_tags & photo_tags
    score += 2.0 * len(overlap)

    # Qualidade
    score += 0.5 * float(tags.get("quality_score") or 0)

    # Anti-rosto (preferência editorial)
    if tags.get("face_visible"):
        score -= 1.0

    # Penalização por upload muito recente (evita repetir várias notícias seguidas
    # com a mesma foto — mesmo que ainda esteja available, se foi uploaded há <3d
    # baixa um pouco a prioridade)
    now = now or datetime.now()
    try:
        up = datetime.fromisoformat(entry.get("uploaded_at", ""))
        age_days = (now - up).total_seconds() / 86400
        if age_days < 3:
            score -= (3 - age_days) * 0.5
    except (ValueError, TypeError):
        pass

    return score


def lookup_for_news(title: str, summary: str, modality: str,
                    min_score: float = 4.0) -> Optional[dict]:
    """Devolve entry da melhor foto available do pool, ou None se nenhuma bate.

    Score mínimo: filtra fotos sem nenhuma relevância (evita devolver foto
    de natação pra notícia de ciclismo só porque pool tá cheio dela).
    """
    state = load_pool_state()
    entries = [e for e in state.get("entries", {}).values()
               if e.get("status") == "available"]
    if not entries:
        return None
    tokens = _extract_keywords(f"{title} {summary}")
    query_tags = _expand_to_tags(tokens)

    scored = [(_score_entry(e, query_tags, modality), e) for e in entries]
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if best_score < min_score:
        return None
    return best


# ─── Claim flow (aid → sha) ───────────────────────────────────────────
# visual.py registra a foto que serviu pra um aid (pré-dispatch).
# feed_post.dispatch_one chama commit_claim depois do preview ser enviado.
# Claims sem commit em >24h são auto-limpos.

CLAIM_TTL_HOURS = 24


def claim(aid: str, sha: str) -> None:
    """Registra que a foto `sha` foi servida pro aid (pré-dispatch)."""
    state = load_pool_state()
    claims = state.setdefault("claims", {})
    claims[aid] = {"sha": sha, "claimed_at": datetime.now().isoformat(timespec="seconds")}
    # Limpa claims vencidos (>TTL) pra não acumular lixo
    cutoff = datetime.now() - timedelta(hours=CLAIM_TTL_HOURS)
    stale = []
    for k, v in claims.items():
        try:
            t = datetime.fromisoformat(v.get("claimed_at", ""))
        except ValueError:
            stale.append(k)
            continue
        if t < cutoff:
            stale.append(k)
    for k in stale:
        claims.pop(k, None)
    save_pool_state(state)


def commit_claim(aid: str) -> bool:
    """Confirma que aid foi dispatched → marca a foto reivindicada como used.
    Limpa o claim. Idempotente. Retorna True se mark_used rolou."""
    state = load_pool_state()
    claims = state.get("claims", {})
    info = claims.get(aid)
    if not info:
        return False
    sha = info.get("sha")
    if not sha:
        claims.pop(aid, None)
        save_pool_state(state)
        return False
    # Remove claim ANTES do mark_used (evita commit duplicado em retry)
    claims.pop(aid, None)
    save_pool_state(state)
    return mark_used(sha)


# ─── Mark used + delete ───────────────────────────────────────────────

def mark_used(sha: str) -> bool:
    """Marca foto como usada — deleta do R2, status=used, last_used_at=now.
    Foto re-elegível depois de COOLDOWN_DAYS dias."""
    state = load_pool_state()
    entry = state.get("entries", {}).get(sha)
    if not entry:
        print(f"⚠ pool.mark_used: sha {sha[:8]} não está no pool")
        return False
    if entry.get("status") == "used":
        return True  # idempotente
    _delete_one(entry)  # mesmo se delete falhar, marcamos used
    entry["status"] = "used"
    entry["last_used_at"] = datetime.now().isoformat(timespec="seconds")
    save_pool_state(state)
    print(f"✓ pool: {sha[:8]} marcado used (cooldown {COOLDOWN_DAYS}d)")
    return True


# ─── Auto-index novas fotos ───────────────────────────────────────────

def auto_index_new_photos() -> int:
    """Detecta fotos novas em DIOGO_DIR (não no manifest) e indexa via Vision.
    Costo ~$0.001/foto. Retorna número indexado."""
    try:
        # reusa o script de indexação como módulo
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "index_diogo_bank",
            ROOT / "scripts" / "index_diogo_bank.py",
        )
        if not spec or not spec.loader:
            return 0
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        photos = mod._photos()  # type: ignore[attr-defined]
        manifest = load_manifest()
        indexed = manifest.setdefault("photos", {})
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            return 0
        n_ok = 0
        for p in photos:
            try:
                sha = mod._sha1(p)  # type: ignore[attr-defined]
            except Exception:
                continue
            if sha in indexed:
                continue
            try:
                size = p.stat().st_size
                if size > mod.MAX_BYTES_FOR_API:  # type: ignore[attr-defined]
                    continue
                img = p.read_bytes()
            except OSError:
                continue
            result = mod._ask_gemini(img, mod._mime_for(p), key)  # type: ignore[attr-defined]
            if not result:
                continue
            indexed[sha] = {
                "path": str(p),
                "filename": p.name,
                "size_bytes": size,
                "mtime": int(p.stat().st_mtime),
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
                **result,
            }
            n_ok += 1
        if n_ok:
            tmp = MANIFEST_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(MANIFEST_PATH)
        return n_ok
    except Exception as e:  # noqa: BLE001
        print(f"⚠ pool.auto_index_new erro: {e!r}")
        return 0


# ─── Status (debug) ───────────────────────────────────────────────────

def status() -> dict:
    state = load_pool_state()
    entries = state.get("entries", {})
    by_status: dict[str, int] = {}
    by_theme: dict[str, int] = {}
    for e in entries.values():
        by_status[e.get("status", "?")] = by_status.get(e.get("status", "?"), 0) + 1
        by_theme[e.get("theme", "?")] = by_theme.get(e.get("theme", "?"), 0) + 1
    manifest = load_manifest()
    return {
        "manifest_total": len(manifest.get("photos", {})),
        "pool_total": len(entries),
        "pool_by_status": by_status,
        "pool_by_theme": by_theme,
        "available": by_status.get("available", 0),
        "used": by_status.get("used", 0),
        "last_refill_at": state.get("last_refill_at"),
    }
