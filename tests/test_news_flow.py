"""Testes determinísticos do fluxo news (sem APIs externas).

Cobre:
  1. feed_post._pick_top_unused respeita MIN_SCORE e filtros de uso
  2. stories._next_unused_from_pool exclui used_in_feed E used_in_story
  3. Coordenação: feed_post marca antes de stories rodar → sem overlap
  4. Catch-up: depois das 8/14h dispara se não rodou hoje; tarde tem prioridade
  5. Pool retention: items <36h vivem; >36h saem (testado em watcher)

Mocka writer/reviewer/render/api pra rodar sem ANTHROPIC_API_KEY_MERGE.

Uso: PYTHONPATH=src python3 tests/test_news_flow.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

# Garantir src/ no path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TZ = ZoneInfo("America/Sao_Paulo")


# ----------------- helpers -----------------

class TestState:
    """Manage tmp output dir via monkey-patching module ROOT pointers."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="merge_test_")
        self.output = Path(self.tmp) / "output"
        self.output.mkdir(parents=True)
        self.pool_file = self.output / "news_pool.json"
        # Patch module paths
        from news import stories, feed_post
        self._patches = []
        self._patches.append(mock.patch.object(stories, "POOL_FILE", self.pool_file))
        self._patches.append(mock.patch.object(stories, "STORY_STATE_MORNING", self.output / ".sm.txt"))
        self._patches.append(mock.patch.object(stories, "STORY_STATE_AFTERNOON", self.output / ".sa.txt"))
        self._patches.append(mock.patch.object(feed_post, "POOL_FILE", self.pool_file))
        self._patches.append(mock.patch.object(feed_post, "STATE_MORNING", self.output / ".fm.txt"))
        self._patches.append(mock.patch.object(feed_post, "STATE_AFTERNOON", self.output / ".fa.txt"))
        for p in self._patches:
            p.start()

    def write_pool(self, items: list[dict]) -> None:
        self.pool_file.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_pool(self) -> list[dict]:
        if not self.pool_file.exists():
            return []
        return json.loads(self.pool_file.read_text(encoding="utf-8"))

    def cleanup(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_item(idx: int, score: float, used_in_story=False, used_in_feed=False) -> dict:
    return {
        "hash": f"h{idx:03d}",
        "title": f"Notícia número {idx}",
        "summary": f"Resumo da notícia {idx}.",
        "feed_name": "TestFeed",
        "category": "br",
        "modalities": ["corrida"],
        "primary_modality": "corrida",
        "score": score,
        "post_event": False,
        "used_in_story": used_in_story,
        "used_in_feed": used_in_feed,
        "added_at": datetime.now().isoformat(),
    }


# ----------------- testes -----------------

PASS = "✓"
FAIL = "✗"
results = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = PASS if cond else FAIL
    line = f"  {mark} {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    results.append((name, cond))


def test_1_pick_top_unused_respects_min_score():
    print("\n[1] feed_post._pick_top_unused respeita MIN_SCORE e filtros")
    s = TestState()
    try:
        from news import feed_post
        # Pool com 5 items: scores variando, alguns já usados
        pool = [
            make_item(1, 8.5),                           # candidato top
            make_item(2, 7.0),                           # candidato
            make_item(3, 5.5),                           # < MIN_SCORE (6.0)
            make_item(4, 9.0, used_in_feed=True),        # já usado em feed
            make_item(5, 8.0, used_in_story=True),       # já usado em story
        ]
        s.write_pool(pool)
        picked = feed_post._pick_top_unused()
        check("retorna o top score elegível",
              picked is not None and picked["hash"] == "h001",
              f"picked={picked['hash'] if picked else None}")
        # Marca como usado e tenta de novo
        feed_post._mark_used_in_feed("h001")
        picked2 = feed_post._pick_top_unused()
        check("após marcar top, retorna o próximo",
              picked2 is not None and picked2["hash"] == "h002",
              f"picked2={picked2['hash'] if picked2 else None}")
        # Marca todos elegíveis
        feed_post._mark_used_in_feed("h002")
        picked3 = feed_post._pick_top_unused()
        check("sem candidato score≥6, retorna None",
              picked3 is None,
              f"picked3={picked3}")
    finally:
        s.cleanup()


def test_2_stories_excludes_both_used_flags():
    print("\n[2] stories._next_unused_from_pool exclui used_in_feed E used_in_story")
    s = TestState()
    try:
        from news import stories
        pool = [
            make_item(1, 9.0, used_in_feed=True),    # excluído
            make_item(2, 8.0, used_in_story=True),   # excluído
            make_item(3, 7.5),                       # candidato 1
            make_item(4, 7.0),                       # candidato 2
            make_item(5, 6.5),                       # candidato 3
        ]
        s.write_pool(pool)
        picked = stories._next_unused_from_pool(2)
        ids = [p["hash"] for p in picked]
        check("pega top 2 não usados",
              ids == ["h003", "h004"],
              f"ids={ids}")
    finally:
        s.cleanup()


def test_3_coordination_no_overlap():
    print("\n[3] Coordenação feed_post → stories: sem overlap em mesmo slot")
    s = TestState()
    try:
        from news import feed_post, stories
        # Pool com 3 candidatos top
        pool = [make_item(i, 10.0 - i * 0.1) for i in range(1, 6)]
        s.write_pool(pool)

        # Simula feed_post pegar e marcar (dispatch)
        item = feed_post._pick_top_unused()
        check("feed_post pega top (h001 score 9.9)",
              item["hash"] == "h001")
        feed_post._mark_used_in_feed(item["hash"])

        # Agora stories deve pegar h002 e h003 (não h001)
        stories_picks = stories._next_unused_from_pool(2)
        ids = [p["hash"] for p in stories_picks]
        check("stories pula o item do feed_post e pega h002, h003",
              ids == ["h002", "h003"],
              f"ids={ids}")

        # Verifica state final do pool
        final_pool = s.read_pool()
        h001 = next(p for p in final_pool if p["hash"] == "h001")
        check("h001 ficou marcado used_in_feed=True",
              h001.get("used_in_feed") is True)
        check("h001 NÃO marcado used_in_story (stories não pegou)",
              not h001.get("used_in_story"))
    finally:
        s.cleanup()


def test_4_catchup_logic():
    print("\n[4] Catch-up: depois das 8/14h dispara se não rodou hoje")
    s = TestState()
    try:
        from news import feed_post, stories
        cases = [
            (7, None, None),                          # antes de 8h: nada
            (8, "morning", "morning"),                # 8h: manhã
            (10, "morning", "morning"),               # 10h: ainda manhã (catch-up)
            (13, "morning", "morning"),               # 13h: ainda manhã (catch-up)
            (14, "afternoon", "afternoon"),           # 14h: tarde (manhã ainda pendente, mas tarde tem prioridade)
            (20, "afternoon", "afternoon"),           # 20h: tarde
        ]
        for hour, exp_feed, exp_story in cases:
            now = datetime(2026, 5, 7, hour, 0, tzinfo=TZ)
            f_slot = feed_post._slot_state_file(now)
            s_slot = stories._slot_state_file(now)
            f_label = None if f_slot is None else ("morning" if "fm" in f_slot.name else "afternoon")
            s_label = None if s_slot is None else ("morning" if "sm" in s_slot.name else "afternoon")
            check(f"h={hour:02d} feed→{f_label} story→{s_label}",
                  f_label == exp_feed and s_label == exp_story,
                  f"esperado feed={exp_feed} story={exp_story}")

        # Agora simula que manhã rodou. Às 13h não dispara mais.
        today = datetime(2026, 5, 7, 13, 0, tzinfo=TZ).date().isoformat()
        feed_post.STATE_MORNING.write_text(today)
        stories.STORY_STATE_MORNING.write_text(today)
        now13 = datetime(2026, 5, 7, 13, 0, tzinfo=TZ)
        check("após manhã rodada, 13h não retorna nada",
              feed_post._slot_state_file(now13) is None
              and stories._slot_state_file(now13) is None)

        # Mas às 14h, dispara tarde
        now14 = datetime(2026, 5, 7, 14, 0, tzinfo=TZ)
        f14 = feed_post._slot_state_file(now14)
        s14 = stories._slot_state_file(now14)
        check("14h ainda dispara tarde apesar de manhã rodada",
              f14 is not None and "fa" in f14.name
              and s14 is not None and "sa" in s14.name)
    finally:
        s.cleanup()


def test_5_idempotency_within_slot():
    print("\n[5] Idempotência: 2 ticks no mesmo slot disparam só 1 vez")
    s = TestState()
    try:
        from news import feed_post
        pool = [make_item(1, 8.0), make_item(2, 7.0)]
        s.write_pool(pool)

        # Mock writer/reviewer/runner/api — não queremos chamadas externas
        with mock.patch.object(feed_post, "_build_brief") as build_mock, \
             mock.patch.object(feed_post.autogen_runner, "_save_brief_json"), \
             mock.patch.object(feed_post.autogen_runner, "_append_caption_md"), \
             mock.patch.object(feed_post.autogen_runner, "_render_brief", return_value=(True, "")), \
             mock.patch.object(feed_post.autogen_runner, "_create_approval", return_value="aid_test"), \
             mock.patch.object(feed_post.bot_state, "read_approval", return_value={}), \
             mock.patch.object(feed_post.bot_state, "write_approval"), \
             mock.patch.object(feed_post, "_send_preview"):
            build_mock.return_value = (
                {"id": "test_brief", "vars": {}, "template": "feature", "caption_md": ""},
                {"scheduled_at": "x", "modality": "corrida"},
                {"warnings": [], "blockers": []}
            )
            now8 = datetime(2026, 5, 7, 8, 0, tzinfo=TZ)
            n1 = feed_post.maybe_dispatch(now8)
            n2 = feed_post.maybe_dispatch(now8)  # mesmo tick
            n3 = feed_post.maybe_dispatch(datetime(2026, 5, 7, 9, 0, tzinfo=TZ))  # 1h depois mesmo dia
            check("1º dispatch envia 1", n1 == 1, f"n1={n1}")
            check("2º dispatch (mesmo slot) envia 0", n2 == 0, f"n2={n2}")
            check("3º dispatch (9h, manhã já rodou) envia 0", n3 == 0, f"n3={n3}")
    finally:
        s.cleanup()


def test_6_empty_pool_marks_slot():
    print("\n[6] Pool vazio: marca slot pra não retentar e notifica")
    s = TestState()
    try:
        from news import feed_post
        s.write_pool([])
        with mock.patch.object(feed_post, "notify") as notify_mock:
            now = datetime(2026, 5, 7, 8, 0, tzinfo=TZ)
            n = feed_post.maybe_dispatch(now)
            check("retorna 0", n == 0)
            check("notifica pool vazio", notify_mock.called,
                  f"call_args={notify_mock.call_args}")
            check("state file marcado",
                  feed_post.STATE_MORNING.exists()
                  and feed_post.STATE_MORNING.read_text().strip() == now.date().isoformat())
    finally:
        s.cleanup()


def test_7_full_day_simulation():
    print("\n[7] Simulação dia completo: 6 items pool, 8h e 14h cada")
    s = TestState()
    try:
        from news import feed_post, stories
        # Pool com 6 items, scores 9.0..7.5
        pool = [make_item(i, 9.0 - (i - 1) * 0.3) for i in range(1, 7)]
        s.write_pool(pool)

        # Mock pesados
        common_mocks = mock.patch.multiple(
            feed_post,
            _build_brief=mock.DEFAULT,
            _send_preview=mock.DEFAULT,
        )
        feed_runner_mocks = mock.patch.multiple(
            feed_post.autogen_runner,
            _save_brief_json=mock.DEFAULT,
            _append_caption_md=mock.DEFAULT,
            _render_brief=mock.DEFAULT,
            _create_approval=mock.DEFAULT,
        )
        bot_state_mocks = mock.patch.multiple(
            feed_post.bot_state,
            read_approval=mock.DEFAULT,
            write_approval=mock.DEFAULT,
        )
        stories_mocks = mock.patch.multiple(
            stories,
            _make_story_brief=mock.DEFAULT,
            _save_brief_json=mock.DEFAULT,
            _render_story_only=mock.DEFAULT,
            _create_approval=mock.DEFAULT,
            _preview_story=mock.DEFAULT,
        )

        with common_mocks as cm, feed_runner_mocks as frm, bot_state_mocks as bsm, stories_mocks as sm:
            cm["_build_brief"].return_value = (
                {"id": "fb", "vars": {}, "template": "feature", "caption_md": ""},
                {"scheduled_at": "x", "modality": "corrida"},
                {"warnings": [], "blockers": []},
            )
            frm["_render_brief"].return_value = (True, "")
            frm["_create_approval"].return_value = "aid_x"
            bsm["read_approval"].return_value = {}
            sm["_make_story_brief"].side_effect = lambda item: {"id": f"s_{item['hash']}"}
            sm["_render_story_only"].return_value = True
            sm["_create_approval"].return_value = "aid_s"

            # Slot manhã 8h
            now8 = datetime(2026, 5, 7, 8, 0, tzinfo=TZ)
            n_feed_m = feed_post.maybe_dispatch(now8)
            n_story_m = stories.maybe_dispatch(now8)
            check("manhã: feed enviou 1", n_feed_m == 1, f"n_feed_m={n_feed_m}")
            check("manhã: stories enviou 2", n_story_m == 2, f"n_story_m={n_story_m}")

            # Verifica que h001 (top) foi pro feed, h002+h003 pra stories
            pool_after_m = s.read_pool()
            h1 = next(p for p in pool_after_m if p["hash"] == "h001")
            h2 = next(p for p in pool_after_m if p["hash"] == "h002")
            h3 = next(p for p in pool_after_m if p["hash"] == "h003")
            check("h001 marcado used_in_feed", h1.get("used_in_feed") is True)
            # Stories só marca em on_approve/reject (mock não dispara), então h2/h3 ficam unmarked
            # Mas para próximo slot precisamos verificar que não vão repetir
            # NOTE: este é o comportamento atual de stories — só marca após aprovação

            # Slot tarde 14h
            now14 = datetime(2026, 5, 7, 14, 0, tzinfo=TZ)
            n_feed_a = feed_post.maybe_dispatch(now14)
            n_story_a = stories.maybe_dispatch(now14)
            check("tarde: feed enviou 1", n_feed_a == 1, f"n_feed_a={n_feed_a}")
            check("tarde: stories enviou 2", n_story_a == 2, f"n_story_a={n_story_a}")

            # Verifica que feed pegou h002 (h001 já era used_in_feed)
            pool_after_a = s.read_pool()
            feed_used = [p for p in pool_after_a if p.get("used_in_feed")]
            feed_used_hashes = sorted(p["hash"] for p in feed_used)
            check("feed marcou h001 e h002 (não duplicou)",
                  feed_used_hashes == ["h001", "h002"],
                  f"feed_used={feed_used_hashes}")

            # Stories sem aprovação ainda → poderia repetir h2/h3 às 14h
            # Esta é a limitação conhecida do design atual de stories.py
            # (stories só marca usado em on_approve/reject; sem ação do user, repete)
            # Vou registrar como AVISO, não falha:
            print("    ⓘ Aviso: stories só marca used_in_story em aprovar/rejeitar.")
            print("       Sem ação do usuário entre 8h e 14h, top items podem reaparecer.")
            print("       Limitação herdada (não introduzida pela mudança de feed_news).")
    finally:
        s.cleanup()


# ----------------- runner -----------------

if __name__ == "__main__":
    print("=" * 60)
    print("Testes do fluxo news (sem APIs externas)")
    print("=" * 60)

    test_1_pick_top_unused_respects_min_score()
    test_2_stories_excludes_both_used_flags()
    test_3_coordination_no_overlap()
    test_4_catchup_logic()
    test_5_idempotency_within_slot()
    test_6_empty_pool_marks_slot()
    test_7_full_day_simulation()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    print(f"Resultado: {passed} passed, {failed} failed (de {len(results)})")
    if failed:
        print("\nFalhas:")
        for name, ok in results:
            if not ok:
                print(f"  ✗ {name}")
        sys.exit(1)
    print("Todos passaram.")
    sys.exit(0)
