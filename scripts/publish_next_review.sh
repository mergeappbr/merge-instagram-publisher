#!/bin/bash
# Publica o próximo review pendente da fila (cadência dom/ter/qui).
#
# Verifica output/published.csv via scheduler.published_post_ids() e
# pula reviews já publicados. Materializa PNGs dataless do iCloud
# copiando de output/posts/<slug>/ quando necessário.
#
# Uso:
#   publish_next_review.sh            # escolhe próximo da fila
#   publish_next_review.sh <slug>     # força slug específico (se pendente)
#
# Loga em output/cron_publish.log
set -e

ROOT="${MERGE_ROOT:-/Users/pedrowanderleyalmeida/Desktop/Merge}"
if [ -x "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3" ]; then
  PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
else
  PYTHON="python3"
fi
LOG="$ROOT/output/cron_publish.log"

# Fila ordenada — cadência dom/ter/qui às 11:45.
# Reviews já publicados (oura, forerunner, roka) são filtrados via published.csv.
QUEUE=(
  oura_ring_gen4
  geis_br
  recovery_tipos
  super_shoes_2026
  wearables_2026
  fitbit_vs_whoop
  canyon_aeroad_cfr
  edge_1050_vs_coros_dura
  theragun_pro_plus
  super_capacetes_road
  assioma_pro_mx
)

cd "$ROOT"

FORCE_SLUG="${1:-}"

# Lista de já publicados — varre output/published.csv procurando qualquer
# slug da fila em qualquer coluna (schema do CSV não é confiável).
PUBLISHED=$("$PYTHON" -c "
import pathlib, re
csv_path = pathlib.Path('output/published.csv')
if not csv_path.exists():
    raise SystemExit()
text = csv_path.read_text(encoding='utf-8')
queue = ['oura_ring_gen4','geis_br','recovery_tipos','super_shoes_2026','wearables_2026','fitbit_vs_whoop','canyon_aeroad_cfr','edge_1050_vs_coros_dura','theragun_pro_plus','super_capacetes_road','assioma_pro_mx','forerunner_amoled','roka_maverick_ii']
for slug in queue:
    if re.search(r'\b' + re.escape(slug) + r'\b', text):
        print(slug)
")

is_published() {
  echo "$PUBLISHED" | grep -Fxq "$1"
}

# Escolhe slug alvo
if [ -n "$FORCE_SLUG" ]; then
  TARGET="$FORCE_SLUG"
  if is_published "$TARGET"; then
    echo "⚠ $TARGET já está em published.csv — abortando" | tee -a "$LOG"
    exit 1
  fi
else
  TARGET=""
  for slug in "${QUEUE[@]}"; do
    if ! is_published "$slug"; then
      TARGET="$slug"
      break
    fi
  done
  if [ -z "$TARGET" ]; then
    echo "✓ fila vazia — nenhum review pendente" | tee -a "$LOG"
    exit 0
  fi
fi

# Materializa PNGs dataless: pra cada output/feed/<slug>*.png que estiver
# dataless (read=0), copia da source em output/posts/<slug>/<NN>_*.png
"$PYTHON" - "$TARGET" <<'PY'
import sys, os, pathlib, shutil, re
slug = sys.argv[1]
feed = pathlib.Path("output/feed")
posts = pathlib.Path(f"output/posts/{slug}")

feed_pngs = sorted(feed.glob(f"{slug}*.png"), key=lambda p: (
    int(p.stem.split('.')[1]) if '.' in p.stem and p.stem.split('.')[1].isdigit() else 1
))
if not feed_pngs:
    sys.exit(f"✗ nenhum PNG em output/feed/{slug}*.png")

source_pngs = sorted(posts.glob("*.png")) if posts.exists() else []

def is_dataless(p):
    try:
        with open(p, "rb") as f:
            return len(f.read(8)) == 0
    except Exception:
        return True

for idx, fp in enumerate(feed_pngs):
    if is_dataless(fp):
        if idx < len(source_pngs):
            src = source_pngs[idx]
            print(f"  materializando {fp.name} ← {src}")
            os.remove(fp)
            shutil.copyfile(src, fp)
        else:
            sys.exit(f"✗ {fp} dataless e sem source em output/posts/{slug}/")
print(f"✓ {len(feed_pngs)} slide(s) prontos pra upload")
PY

{
  echo
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') · publish_review $TARGET ==="
  "$PYTHON" src/publish.py output/feed \
      --post "$TARGET" \
      --caption-file "output/feed/_captions/${TARGET}.txt"
} 2>&1 | tee -a "$LOG"
