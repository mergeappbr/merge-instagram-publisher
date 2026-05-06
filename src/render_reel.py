"""
Merge Creator — renderer de Reels (HTML animado → MP4).

Pipeline:
  1. Carrega template HTML com animações CSS + JS
  2. Playwright abre Chromium em 1080×1920 com gravação WebM
  3. Aguarda a duração da animação (segura render)
  4. Converte WebM → MP4 H.264 via ffmpeg (Instagram-ready)

Uso:
  python src/render_reel.py reel_sub2          # renderiza um reel específico
  python src/render_reel.py reel_sub2 --duration 9
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
OUT_REELS = ROOT / "output" / "reels"
OUT_TMP = ROOT / "output" / "_tmp_reels"

WIDTH = 1080
HEIGHT = 1920
FPS = 30


def render_reel(name: str, duration_s: float = 8.0) -> Path:
    template = TEMPLATES / f"{name}.html"
    if not template.exists():
        raise FileNotFoundError(f"Template não encontrado: {template}")

    OUT_REELS.mkdir(parents=True, exist_ok=True)
    OUT_TMP.mkdir(parents=True, exist_ok=True)

    final_mp4 = OUT_REELS / f"{name}.mp4"

    # Init script: pausa todas CSS animations enquanto <body> não tem .go.
    # Templates com JS counter devem checar `document.body.classList.contains('go')`
    # antes de começar — se não tiver, escutar o MutationObserver até a classe aparecer.
    init_pause = """
    (() => {
      const style = document.createElement('style');
      style.id = '__merge_pause__';
      style.textContent = 'body:not(.go) *, body:not(.go) *::before, body:not(.go) *::after { animation-play-state: paused !important; }';
      const inject = () => {
        if (document.documentElement && !document.getElementById('__merge_pause__')) {
          document.documentElement.appendChild(style);
        }
      };
      inject();
      new MutationObserver(inject).observe(document.documentElement || document, {childList: true, subtree: true});
    })();
    """

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        record_start = time.monotonic()
        ctx = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            record_video_dir=str(OUT_TMP),
            record_video_size={"width": WIDTH, "height": HEIGHT},
        )
        ctx.add_init_script(init_pause)
        page = ctx.new_page()
        page.goto(template.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        # Pequena pausa pra fontes/imagens settle
        time.sleep(0.3)

        # Aqui as animações estão pausadas em t=0. Disparamos o início via .go no body.
        begin_offset = time.monotonic() - record_start
        page.evaluate("document.body.classList.add('go')")

        # Roda a animação por `duration_s` segundos
        time.sleep(duration_s)

        # Fechar a página força o Playwright a finalizar o vídeo
        page.close()
        ctx.close()
        browser.close()

    # Encontra o WebM mais recente em _tmp_reels
    webm_files = sorted(OUT_TMP.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webm_files:
        raise RuntimeError("Playwright não gerou WebM")
    webm_path = webm_files[0]

    # Converte WebM → MP4 H.264 via ffmpeg + trim
    # Como CSS/JS são pausados até __mergeBegin, o início da animação corresponde
    # exatamente ao instante `begin_offset` (medido em time.monotonic).
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()

    # Probe da duração real do WebM (debug + sanity)
    probe = subprocess.run(
        [ffmpeg_bin, "-i", str(webm_path), "-hide_banner"],
        capture_output=True, text=True
    )
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    webm_dur = (int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))) if m else 0.0
    setup_offset = max(0.0, begin_offset)
    print(f"   webm={webm_dur:.2f}s · begin_offset={begin_offset:.2f}s · trim=-ss {setup_offset:.2f} -t {duration_s}")

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(webm_path),
        "-ss", f"{setup_offset:.3f}",
        "-t", f"{duration_s:.3f}",
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-movflags", "+faststart",
        "-an",  # sem audio (Instagram aceita; pode adicionar trilha depois)
        str(final_mp4),
    ]
    print(f"→ ffmpeg: WebM → MP4 ({final_mp4.name})")
    subprocess.run(cmd, check=True, capture_output=True)

    # Limpa WebM temporário
    shutil.rmtree(OUT_TMP, ignore_errors=True)

    return final_mp4


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="Nome do template (sem .html)")
    parser.add_argument("--duration", type=float, default=8.0, help="Duração em segundos")
    args = parser.parse_args(argv)

    out = render_reel(args.name, args.duration)
    print(f"   ✓ {out.relative_to(ROOT)}")
    print(f"   tamanho: {out.stat().st_size / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
