#!/usr/bin/env python3
"""Sync seletivo do estado Merge → vault Obsidian.

Roda local (não no Railway). Lê CSVs/JSONs/configs do repo e escreve
Markdown estruturado dentro do vault. Idempotente — sobrescreve arquivos
"auto-generated" e preserva os que o usuário criou manualmente.

Uso:
    python3 scripts/obsidian_sync.py              # sync completo
    python3 scripts/obsidian_sync.py --section races

Vault default: ~/Documents/ObsidianVault/Merge
Override:      OBSIDIAN_VAULT=/path/to/vault python3 ...
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = Path.home() / "Documents" / "ObsidianVault" / "Merge"
VAULT = Path(os.environ.get("OBSIDIAN_VAULT", DEFAULT_VAULT))

AUTO_HEADER = (
    "<!-- AUTO-GENERATED por scripts/obsidian_sync.py · não editar à mão "
    "(será sobrescrito no próximo sync). Edits manuais → mover pra outro arquivo. -->\n\n"
)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---- Calendar -------------------------------------------------------------

def sync_calendar() -> None:
    csv_path = ROOT / "content" / "calendar.csv"
    out = VAULT / "Operations" / "Calendar.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        out.write_text(AUTO_HEADER + "_calendar.csv ausente_\n", encoding="utf-8")
        return
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    today = datetime.now().date().isoformat()
    upcoming = [r for r in rows if r.get("scheduled_at", "")[:10] >= today]
    past = [r for r in rows if r.get("scheduled_at", "")[:10] < today][-30:]

    lines = [
        AUTO_HEADER,
        "---",
        "type: dashboard",
        f"updated: {_stamp()}",
        "tags: [operations, calendar]",
        "---",
        "",
        "# 📅 Calendar",
        f"Total slots: {len(rows)} · upcoming: {len(upcoming)} · last 30 published: {len(past)}",
        "",
        "## Upcoming",
        "| # | when | format | id | theme | label |",
        "|---|---|---|---|---|---|",
    ]
    for r in upcoming[:50]:
        lines.append(
            f"| {r.get('idx','')} | `{r.get('scheduled_at','')}` | {r.get('format','')} "
            f"| `{r.get('id','')}` | {r.get('theme','')} | {r.get('label','')} |"
        )
    lines += ["", "## Last 30 published"]
    lines += ["| when | id | format | theme |", "|---|---|---|---|"]
    for r in past[::-1]:
        lines.append(
            f"| `{r.get('scheduled_at','')}` | `{r.get('id','')}` | "
            f"{r.get('format','')} | {r.get('theme','')} |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {out.relative_to(VAULT)}")


# ---- Published log --------------------------------------------------------

def sync_published() -> None:
    csv_path = ROOT / "output" / "published.csv"
    out = VAULT / "Operations" / "Published Log.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        out.write_text(AUTO_HEADER + "_published.csv ainda vazio_\n", encoding="utf-8")
        return
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    rows.sort(key=lambda r: r.get("published_at", ""), reverse=True)
    lines = [
        AUTO_HEADER,
        "---",
        "type: dashboard",
        f"updated: {_stamp()}",
        "tags: [operations, published]",
        "---",
        "",
        "# 📤 Published Log",
        f"Total: {len(rows)} posts. Mostrando últimos 100.",
        "",
        "| when | id | format | media_id | url |",
        "|---|---|---|---|---|",
    ]
    for r in rows[:100]:
        lines.append(
            f"| `{r.get('published_at','')[:16]}` | `{r.get('id','')}` "
            f"| {r.get('format','')} | `{r.get('media_id','')}` "
            f"| {r.get('permalink','') or '—'} |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {out.relative_to(VAULT)}")


# ---- News pool ------------------------------------------------------------

def sync_news_pool() -> None:
    p = ROOT / "output" / "news_pool.json"
    out = VAULT / "News" / "Pool Snapshot.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        out.write_text(AUTO_HEADER + "_news_pool.json vazio_\n", encoding="utf-8")
        return
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        items = []
    items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    lines = [
        AUTO_HEADER,
        "---",
        "type: dashboard",
        f"updated: {_stamp()}",
        "tags: [news, pool]",
        "---",
        "",
        "# 📰 News Pool Snapshot",
        f"Total no pool: {len(items)}.",
        "",
        "| score | post_event | feed | título | usado | modality |",
        "|---|---|---|---|---|---|",
    ]
    for it in items[:80]:
        used = []
        if it.get("used_in_feed"):
            used.append("feed")
        if it.get("used_in_story"):
            used.append("story")
        used_str = ",".join(used) or "—"
        title = (it.get("title") or "")[:80].replace("|", "\\|")
        lines.append(
            f"| {it.get('score','?')} | {'✓' if it.get('post_event') else ''} "
            f"| {it.get('feed_name','')} | {title} | {used_str} "
            f"| {it.get('primary_modality','')} |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {out.relative_to(VAULT)}")


# ---- News sources ---------------------------------------------------------

def sync_news_sources() -> None:
    """Lê src/news/feeds.py via import e gera lista de fontes."""
    out = VAULT / "News" / "Sources.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from news.feeds import FEEDS  # type: ignore
    except Exception as e:  # noqa: BLE001
        out.write_text(
            AUTO_HEADER + f"_falha import feeds.py: {e!r}_\n", encoding="utf-8"
        )
        return

    by_cat: dict[str, list[dict]] = {}
    for f in FEEDS:
        by_cat.setdefault(f.get("category", "?"), []).append(f)

    lines = [
        AUTO_HEADER,
        "---",
        "type: doc",
        f"updated: {_stamp()}",
        "tags: [news, sources]",
        "---",
        "",
        "# 📡 News Sources",
        f"Total: {len(FEEDS)} feeds em {len(by_cat)} categorias.",
        "",
    ]
    for cat in sorted(by_cat.keys()):
        lines += [f"## {cat}", "", "| nome | weight | modalities | url |", "|---|---|---|---|"]
        for f in sorted(by_cat[cat], key=lambda x: -x.get("weight_relevance", 0)):
            mods = ", ".join(f.get("modalities", []))
            lines.append(
                f"| {f.get('name','')} | {f.get('weight_relevance','')} "
                f"| {mods} | <{f.get('url','')}> |"
            )
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {out.relative_to(VAULT)}")


# ---- Races ----------------------------------------------------------------

def sync_races() -> None:
    yml = ROOT / "config" / "races.yml"
    out_dir = VAULT / "Races"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not yml.exists():
        return
    try:
        import yaml  # type: ignore
    except ImportError:
        print("⚠ pyyaml não disponível — pulando races")
        return
    data = yaml.safe_load(yml.read_text(encoding="utf-8"))
    races = data.get("races", []) or []

    index_lines = [
        AUTO_HEADER,
        "---",
        "type: index",
        f"updated: {_stamp()}",
        "tags: [races]",
        "---",
        "",
        "# 🏁 Races index",
        f"{len(races)} provas configuradas em `config/races.yml`.",
        "",
        "| id | name | kind | date | location |",
        "|---|---|---|---|---|",
    ]
    for r in races:
        rid = r.get("id", "?")
        index_lines.append(
            f"| [[{rid}\\|{rid}]] | {r.get('name','')} | {r.get('kind','')} "
            f"| {r.get('date','')} | {r.get('location','')} |"
        )
        # Página individual da race
        lines = [
            AUTO_HEADER,
            "---",
            "type: race",
            f"race_id: {rid}",
            f"kind: {r.get('kind','')}",
            f"date: {r.get('date','')}",
            f"updated: {_stamp()}",
            "tags: [race]",
            "---",
            "",
            f"# {r.get('name','?')}",
            f"**Data**: {r.get('date','?')}"
            + (f" → {r['date_end']}" if r.get("date_end") else ""),
            f"**Local**: {r.get('location','?')}",
            f"**Kind**: {r.get('kind','?')} · **Distance**: {r.get('distance','—')}",
            f"**Site**: {r.get('site','—')}",
            f"**Logo**: `brand/{r.get('logo','—')}`"
            + (" (on_dark)" if r.get("logo_on_dark") else ""),
            "",
            "## bg_pool (rotação 60d)",
        ]
        for img in r.get("bg_pool", []) or []:
            lines.append(f"- `{img}`")
        if r.get("bg_results_cover"):
            lines += [
                "",
                "## Results (carrossel T+1)",
                f"- cover: `{r.get('bg_results_cover','')}`",
                f"- male: `{r.get('bg_results_male','')}`",
                f"- female: `{r.get('bg_results_female','')}`",
            ]
        lines += [
            "",
            "## Notas manuais",
            "_(adicionar abaixo — não será sobrescrito desde que não esteja entre os blocos auto)_",
            "",
        ]
        page = out_dir / f"{rid}.md"
        page.write_text("\n".join(lines), encoding="utf-8")

    (out_dir / "_index.md").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"✓ {len(races)} races sincronizadas")


# ---- Insights -------------------------------------------------------------

def sync_insights() -> None:
    csv_path = ROOT / "output" / "insights.csv"
    out = VAULT / "Insights" / "Daily Top.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        out.write_text(AUTO_HEADER + "_insights.csv ainda vazio_\n", encoding="utf-8")
        return
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    # Engagement: likes*1 + comments*3 + saved*2 + shares*4 + views*0.001
    def eng(r: dict) -> float:
        try:
            return (
                float(r.get("likes", 0) or 0)
                + float(r.get("comments", 0) or 0) * 3
                + float(r.get("saved", 0) or 0) * 2
                + float(r.get("shares", 0) or 0) * 4
                + float(r.get("views", 0) or 0) * 0.001
            )
        except (TypeError, ValueError):
            return 0.0

    rows.sort(key=eng, reverse=True)
    lines = [
        AUTO_HEADER,
        "---",
        "type: dashboard",
        f"updated: {_stamp()}",
        "tags: [insights]",
        "---",
        "",
        "# 📊 Insights — Top performers",
        f"Total entradas: {len(rows)}. Score = likes + 3·comments + 2·saved + 4·shares + 0.001·views.",
        "",
        "| score | id | views | likes | saves | shares | comments |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows[:50]:
        lines.append(
            f"| {eng(r):.0f} | `{r.get('media_id','')}` "
            f"| {r.get('views','')} | {r.get('likes','')} "
            f"| {r.get('saved','')} | {r.get('shares','')} "
            f"| {r.get('comments','')} |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {out.relative_to(VAULT)}")


# ---- Main -----------------------------------------------------------------

SECTIONS = {
    "calendar": sync_calendar,
    "published": sync_published,
    "news_pool": sync_news_pool,
    "news_sources": sync_news_sources,
    "races": sync_races,
    "insights": sync_insights,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", choices=list(SECTIONS) + ["all"], default="all")
    args = ap.parse_args()

    if not VAULT.exists():
        print(f"⚠ Vault não existe: {VAULT}")
        return

    targets = SECTIONS.values() if args.section == "all" else [SECTIONS[args.section]]
    for fn in targets:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"⚠ {fn.__name__}: {e!r}")


if __name__ == "__main__":
    main()
