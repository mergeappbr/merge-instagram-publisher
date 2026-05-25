"""
Cliente Claude dedicado da Merge — autogen e news scoring.

Usa env var ANTHROPIC_API_KEY_MERGE (NUNCA reutiliza key da Sofia/Oases).
Falhas de rede são propagadas; quem chama decide se notifica via alerts.

Modelos:
  PRIMARY  — claude-opus-4-6 (planner editorial, reviewer, news scoring de borda)
  FAST     — claude-sonnet-4-6 (writer de brief, scoring rotineiro de news)

Uso:
    from llm import complete
    text = complete(system="...", user="...", fast=True)
    data = complete_json(system="...", user="...")
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import anthropic

API_KEY = os.getenv("ANTHROPIC_API_KEY_MERGE", "").strip()

PRIMARY_MODEL = "claude-opus-4-6"
FAST_MODEL = "claude-sonnet-4-6"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY_MERGE ausente. Configure no Railway."
            )
        _client = anthropic.Anthropic(api_key=API_KEY)
    return _client


def complete(
    *,
    system: str,
    user: str,
    fast: bool = False,
    max_tokens: int = 4000,
    temperature: float = 0.7,
) -> str:
    """Completion simples. Retorna texto bruto."""
    client = _get_client()
    msg = client.messages.create(
        model=FAST_MODEL if fast else PRIMARY_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def complete_json(
    *,
    system: str,
    user: str,
    fast: bool = False,
    max_tokens: int = 8000,
    temperature: float = 0.4,
) -> Any:
    """Completion que devolve JSON. Extrai bloco ```json``` se vier cercado."""
    raw = complete(
        system=system + "\n\nResponda APENAS com JSON válido, sem markdown.",
        user=user,
        fast=fast,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    # Tenta direto
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Tenta extrair bloco ```json``` (com fechamento)
    match = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Strip fence só de abertura (resposta truncada antes do ``` final)
    stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip()).strip()
    if stripped.endswith("```"):
        stripped = stripped[:-3].strip()
    # Tenta achar primeiro { ou [ na resposta crua
    for source in (stripped, raw):
        for opener, closer in (("{", "}"), ("[", "]")):
            start = source.find(opener)
            end = source.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(source[start : end + 1])
                except json.JSONDecodeError:
                    pass
            # Fallback: JSON truncado — escaneia char-por-char fechando braces
            # balanceados até achar um sub-objeto válido.
            if start != -1:
                depth = 0
                in_str = False
                esc = False
                last_valid = -1
                for i, ch in enumerate(source[start:], start=start):
                    if esc:
                        esc = False
                        continue
                    if ch == "\\" and in_str:
                        esc = True
                        continue
                    if ch == '"':
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if ch == opener:
                        depth += 1
                    elif ch == closer:
                        depth -= 1
                        if depth == 0:
                            last_valid = i
                            break
                if last_valid != -1:
                    try:
                        return json.loads(source[start : last_valid + 1])
                    except json.JSONDecodeError:
                        pass
    # Falhou tudo — salva raw num log pra debug
    try:
        import pathlib, datetime
        debug_dir = pathlib.Path("output/llm_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"{ts}_raw.txt").write_text(raw, encoding="utf-8")
    except Exception:
        pass
    raise ValueError(f"resposta não é JSON parseável: {raw[:300]}")
