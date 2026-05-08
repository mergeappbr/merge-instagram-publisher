"""Log de gerações de imagem + relatório semanal no Telegram.

Cada chamada bem-sucedida de `resolve_bg_for_news` registra uma linha
JSONL em `output/.visual_generations.jsonl`. Aos domingos 20:00 BRT, o
scheduler invoca `maybe_weekly_report(now)` que monta o sumário e
manda no Telegram via alerts.notify.

Pricing usado pra estimativa (oficial Google, set/2025):
- Gemini 2.5 Flash Image: $30 / 1M output tokens × 1290 tokens/imagem
  = $0.039 por imagem.
- Pollinations FLUX: $0 (free, rate-limited).
- Wikipedia: $0.

Idempotência: state file `.last_visual_weekly_report.txt` guarda
'YYYY-WW' da última semana enviada — não duplica se Railway reinicia.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "output" / ".visual_generations.jsonl"
STATE_PATH = ROOT / "output" / ".last_visual_weekly_report.txt"

# Custos unitários estimados (USD)
PRICING = {
    "gemini": 0.039,   # Gemini 2.5 Flash Image: 1290 tokens × $30/1M
    "flux": 0.0,       # Pollinations FLUX (free)
    "wiki": 0.0,       # Wikipedia REST
    "unknown": 0.0,
}

REPORT_HOUR = 20  # domingo 20:00 BRT
REPORT_WEEKDAY = 6  # 0=segunda ... 6=domingo (Python convention)

USD_BRL_FALLBACK = 5.50  # usado se awesomeapi falhar


def _fetch_usd_brl() -> float:
    """Cotação USD→BRL via awesomeapi (free, sem auth, ~200ms).
    Fallback pro valor fixo se rede falhar.
    """
    try:
        import httpx
        with httpx.Client(timeout=5) as client:
            r = client.get("https://economia.awesomeapi.com.br/json/last/USD-BRL")
            r.raise_for_status()
            data = r.json()
            bid = float(data["USDBRL"]["bid"])
            if 1.0 < bid < 20.0:  # sanity check
                return bid
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual_report.fx falhou: {e!r}")
    return USD_BRL_FALLBACK


def _engine_from_label(source_label: str) -> str:
    """Extrai engine ('gemini'|'flux'|'wiki'|'unknown') do source_label.

    Labels do visual.py:
    - 'scene:gemini' / 'scene:flux'
    - 'scene-fallback:<entity>:gemini' / ':flux'
    - 'wiki:<entity>'
    """
    if not source_label:
        return "unknown"
    if source_label.startswith("wiki:"):
        return "wiki"
    last = source_label.rsplit(":", 1)[-1]
    if last in ("gemini", "flux"):
        return last
    return "unknown"


def log_generation(aid: str, source_label: str, byte_size: int) -> None:
    """Registra uma geração bem-sucedida. Falha aqui é silenciosa."""
    try:
        engine = _engine_from_label(source_label)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "aid": aid,
            "engine": engine,
            "label": source_label,
            "bytes": byte_size,
            "cost_usd": PRICING.get(engine, 0.0),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual_report.log falhou: {e!r}")


def _read_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    out: list[dict] = []
    try:
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _summarize_window(start: datetime, end: datetime) -> dict:
    """Conta gerações por engine entre [start, end). Datetime naive (local)."""
    entries = _read_log()
    counts = {"gemini": 0, "flux": 0, "wiki": 0, "unknown": 0}
    cost_total = 0.0
    bytes_total = 0
    for e in entries:
        try:
            ts = datetime.fromisoformat(e.get("ts", ""))
        except ValueError:
            continue
        if ts < start or ts >= end:
            continue
        eng = e.get("engine", "unknown")
        counts[eng] = counts.get(eng, 0) + 1
        cost_total += float(e.get("cost_usd") or 0.0)
        bytes_total += int(e.get("bytes") or 0)
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "cost_usd": cost_total,
        "bytes": bytes_total,
        "start": start,
        "end": end,
    }


def _format_report(summary: dict) -> str:
    start = summary["start"]
    end = summary["end"]
    counts = summary["counts"]
    total = summary["total"]
    cost = summary["cost_usd"]
    mb = summary["bytes"] / (1024 * 1024) if summary["bytes"] else 0.0

    period = (
        f"{start.strftime('%d/%m')} → {(end - timedelta(seconds=1)).strftime('%d/%m')}"
    )

    if total == 0:
        return (
            f"📊 <b>Merge · imagens · semana {period}</b>\n\n"
            f"Nenhuma imagem gerada nessa janela."
        )

    gemini_n = counts.get("gemini", 0)
    flux_n = counts.get("flux", 0)
    wiki_n = counts.get("wiki", 0)
    unk_n = counts.get("unknown", 0)

    gemini_cost = gemini_n * PRICING["gemini"]
    fx = _fetch_usd_brl()
    gemini_brl = gemini_cost * fx
    cost_brl = cost * fx

    def _brl(v: float) -> str:
        # 'R$ 1.234,56' (locale-friendly sem dependência de locale)
        s = f"{v:,.2f}"
        return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

    lines = [
        f"📊 <b>Merge · imagens · semana {period}</b>",
        "",
        f"<b>Total:</b> {total} imagens",
        "",
        f"🟢 Gemini 2.5 Flash: <b>{gemini_n}</b> "
        f"(~{_brl(gemini_brl)})",
        f"🟡 FLUX (free): <b>{flux_n}</b>",
        f"📚 Wikipedia: <b>{wiki_n}</b>",
    ]
    if unk_n:
        lines.append(f"❓ outros: {unk_n}")

    lines += [
        "",
        f"<b>Custo estimado:</b> {_brl(cost_brl)} "
        f"<i>(${cost:.2f} · USD/BRL {fx:.2f})</i>",
    ]
    if mb:
        lines.append(f"<b>Tráfego:</b> {mb:.1f} MB")

    # Aviso de crédito gratuito (alinhado com scheduler.GEMINI_CREDIT_*)
    lines += [
        "",
        "<i>Crédito Gemini $300 ativo até 06/08/2026 — "
        "custo estimado é referência.</i>",
    ]

    return "\n".join(lines)


def maybe_weekly_report(now: datetime) -> None:
    """Domingo 20h BRT — manda relatório dos últimos 7 dias.

    Idempotente: state file guarda 'YYYY-WW' da última semana enviada.
    `now` é tz-aware (America/Sao_Paulo). Usamos naive local pra comparar
    com timestamps do log (que são naive `datetime.now().isoformat()`).
    """
    if now.weekday() != REPORT_WEEKDAY:
        return
    if now.hour < REPORT_HOUR:
        return

    iso_year, iso_week, _ = now.isocalendar()
    stamp = f"{iso_year}-W{iso_week:02d}"
    if STATE_PATH.exists():
        try:
            if STATE_PATH.read_text(encoding="utf-8").strip() == stamp:
                return
        except OSError:
            pass

    # Janela: 7 dias terminando em "agora" (local, sem tz pra alinhar com log)
    end_naive = now.replace(tzinfo=None)
    start_naive = end_naive - timedelta(days=7)

    summary = _summarize_window(start_naive, end_naive)
    msg = _format_report(summary)

    try:
        from alerts import notify
        notify(msg, silent=True, force=True)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual_report.notify falhou: {e!r}")
        return

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(stamp, encoding="utf-8")
    print(f"visual_report · semana {stamp} enviada ({summary['total']} imgs)")


def force_report_now() -> str:
    """Útil pra debug/teste manual: monta e devolve o texto sem state check."""
    end_naive = datetime.now()
    start_naive = end_naive - timedelta(days=7)
    summary = _summarize_window(start_naive, end_naive)
    return _format_report(summary)
