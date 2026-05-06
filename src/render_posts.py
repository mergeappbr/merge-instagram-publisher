"""
Merge Posts — modernized Janeiro/26 carousels (HTML → PNG 1080×1350).

Reaproveita os PNGs existentes em ~/Desktop/Merge Posts/Instagram Posts/Janeiro:26/
como source images dentro da nova identidade Merge (dark/light + orange).

Uso:
  python3 src/render_posts.py             # renderiza tudo
  python3 src/render_posts.py shoes       # renderiza só um carrossel
"""
from __future__ import annotations

import sys
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_POSTS = ROOT / "templates" / "posts"
OUT = ROOT / "output" / "posts"
SOURCE_IMG = Path("/Users/pedrowanderleyalmeida/Desktop/Merge Posts/Instagram Posts/Janeiro:26")
STYLES = (TEMPLATES_POSTS / "_post_styles.css").read_text(encoding="utf-8")

W, H = 1080, 1350

# ============== HTML BUILDERS ==============

def page_shell(body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>{STYLES}</style>
</head>
<body>
{body_html}
</body>
</html>"""


def footer(cta: str | None = "descubra", final: bool = False) -> str:
    if final:
        return ""
    arrow = ""
    if cta:
        arrow = f'<span class="cta-arrow"><span class="arrow">→</span><span class="label">({cta})</span></span>'
    else:
        arrow = '<span class="cta-arrow"><span class="arrow">→</span></span>'
    return f"""
<div class="post__footer">
  <span class="brand-mark">merge<span class="dot">.</span></span>
  <span class="handle">@mergeyourlifestyle</span>
  {arrow}
</div>"""


def cover(theme: str, kicker: str, headline_html: str, lead: str, hero_img: str | None, hero_style: str = "", bg_img: str | None = None) -> str:
    bg_html = ""
    if bg_img:
        bg_html = f'<div class="post__bg" style="background-image: url(\'file://{bg_img}\'); background-position: center 50%;"></div><div class="post__bg-overlay"></div>'
    hero_html = ""
    if hero_img:
        hero_html = f'<img class="hero-img" src="file://{hero_img}" style="{hero_style}">'
    body = f"""
<div class="post post--{theme}">
  {bg_html}
  <div class="post__content">
    <span class="kicker"><span class="dot"></span>{kicker}</span>
    <div style="margin-top:48px;">
      <h1 class="headline headline--xl">{headline_html}</h1>
      <p class="body-display" style="margin-top:28px; max-width:880px;">{lead}</p>
    </div>
    {hero_html}
    {footer(cta='descubra')}
  </div>
</div>"""
    return page_shell(body)


def content(theme: str, num: str, title: str, body_html: str, hero_img: str | None = None, hero_style: str = "", bg_img: str | None = None, kicker: str | None = None) -> str:
    bg_html = ""
    if bg_img:
        bg_html = f'<div class="post__bg" style="background-image: url(\'file://{bg_img}\'); background-position: center 50%;"></div><div class="post__bg-overlay"></div>'
    hero_html = ""
    if hero_img:
        hero_html = f'<img class="hero-img" src="file://{hero_img}" style="margin-top:32px; {hero_style}">'
    kicker_html = ""
    if kicker:
        kicker_html = f'<span class="kicker"><span class="dot"></span>{kicker}</span>'
    body = f"""
<div class="post post--{theme}">
  {bg_html}
  <div class="post__content">
    {kicker_html}
    <div class="slide-no" style="margin-top:{'24px' if kicker else '0'};"><span class="num">{num}</span> · {title}</div>
    <div style="margin-top:36px; max-width:880px;">{body_html}</div>
    {hero_html}
    {footer()}
  </div>
</div>"""
    return page_shell(body)


def tier_slide(letter: str, name: str, badge_class: str, blurb: str, items: str, hero_img: str | None = None, hero_style: str = "") -> str:
    hero_html = ""
    if hero_img:
        hero_html = f'<img class="hero-img" src="file://{hero_img}" style="margin-top:24px; {hero_style}">'
    body = f"""
<div class="post post--light">
  <div class="post__content">
    <div style="text-align:center; margin-top:8px;">
      <div class="tier {badge_class}" style="margin: 0 auto;">{letter}</div>
    </div>
    <h2 class="headline headline--md" style="text-align:center; margin-top:28px;">{name}</h2>
    <p class="body-display" style="text-align:center; margin-top:14px; color:#4A4A4A;">{blurb}</p>
    {hero_html}
    <p class="body-mono" style="text-align:center; margin-top:32px; max-width:880px; align-self:center;">{items}</p>
    {footer()}
  </div>
</div>"""
    return page_shell(body)


def closing() -> str:
    body = f"""
<div class="post post--dark">
  <div class="post__content" style="justify-content:flex-end; padding-bottom:160px;">
    <div style="margin-bottom:auto; margin-top:280px;">
      <p class="body-mono" style="font-size:32px; line-height:1.5; max-width:920px; color:#F2F2F2;">
        você provavelmente <b style="color:#FF6900;">não verá</b> esta página novamente.<br>
        siga-nos para que possamos te tornar um atleta melhor.
      </p>
    </div>
    <svg class="final-zlogo" viewBox="0 0 600 480" xmlns="http://www.w3.org/2000/svg">
      <path d="M40 60 L520 60 L460 200 L260 200 L380 200 L320 280 L160 280 L100 360 L420 360 L360 460 L40 460 L40 380 L260 380 L320 300 L120 300 L180 220 L380 220 L440 100 L40 100 Z" fill="#FF6900" opacity="0.95"/>
      <path d="M40 240 L380 240 L320 340 L40 340 Z" fill="#FF6900" opacity="0.95"/>
    </svg>
    <div class="post__footer" style="z-index:5;">
      <span class="brand-mark" style="color:#F2F2F2;">merge<span class="dot">.</span></span>
      <span class="handle">@mergeyourlifestyle</span>
      <span class="cta-arrow"><span class="label">(siga)</span></span>
    </div>
  </div>
</div>"""
    return page_shell(body)


# ============== CAROUSELS ==============

SHOES_DIR = SOURCE_IMG  # all source PNGs
SRC = lambda name: str(SOURCE_IMG / name)

CAROUSELS: dict[str, list[tuple[str, str]]] = {}

# ---------- SHOES ----------
CAROUSELS["shoes"] = [
    ("01_capa", cover(
        theme="dark",
        kicker="ROTAÇÃO · TÊNIS DE CORRIDA",
        headline_html='você precisa de<br><span class="hl">múltiplos</span> tênis<br>de corrida.',
        lead="não é frescura. é prevenção de lesão e ganho de performance.",
        hero_img=None,
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_race.jpg"
    )),
    ("02_repeticao", content(
        theme="light",
        num="01",
        title="invenção de moda?",
        body_html=(
            '<p class="body-mono">correr é repetição.<br>usar o <b>mesmo tênis</b> aplica a mesma tensão nos mesmos tecidos toda vez.</p>'
            '<p class="body-mono" style="margin-top:18px;">a rotação <b>distribui carga</b> em vez de centralizá-la.</p>'
            '<p class="body-mono" style="margin-top:18px;">não é moda. é <b>essencial pra reduzir lesão.</b></p>'
        ),
    )),
    ("03_pau_pra_obra", content(
        theme="light",
        num="02",
        title="o tênis pau pra toda obra.",
        body_html=(
            '<p class="body-mono">o problema não é o tênis ruim.<br>é repetir o <b>mesmo padrão de carga</b> toda vez.</p>'
            '<p class="body-mono" style="margin-top:18px;">um tênis "como uma luva" também:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>sobrecarrega os mesmos tecidos</li>'
            '<li>estressa a mesma estrutura sob fadiga</li>'
            '<li>para de prover estímulos variados</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">os tecidos <b>acumulam estresse silenciosamente</b> até a dor aparecer.</p>'
        ),
    )),
    ("04_revezamento", content(
        theme="light",
        num="03",
        title="construa o revezamento certo.",
        body_html=(
            '<p class="body-mono">não é volume de tênis. é <b>contraste entre eles.</b></p>'
            '<p class="body-mono" style="margin-top:18px;">um revezamento mínimo inclui:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>um <b>macio · maior drop</b> · longos & leves</li>'
            '<li>um <b>responsivo</b> · dias de velocidade</li>'
            '<li>um com <b>geometria diferente</b> · novo estímulo</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">2 tênis com propostas distintas <b>já reduzem o estresse.</b><br>3 é o ideal — não obrigatório.</p>'
        ),
    )),
    ("05_close", closing()),
]

# ---------- FOOD ----------
CAROUSELS["food"] = [
    ("01_capa", cover(
        theme="light",
        kicker="PRÉ-TREINO · TIER LIST",
        headline_html='o que comer<br><span class="hl">antes</span> de correr.',
        lead="ranking dos melhores e piores carbos pré-treino. de S (ouro) a D (evite).",
        hero_img=None,
    )),
    ("02_d", tier_slide(
        letter="D", name="pré-treinos & alta gordura", badge_class="tier--d",
        blurb="alto risco de problemas intestinais e queda de energia.",
        items="comidas com alta gordura · comidas com alta fibra · suplementos de pré-treino",
    )),
    ("03_c", tier_slide(
        letter="C", name="proteína magra solo", badge_class="tier--c",
        blurb="não derruba treino mas não fornece energia rápida.",
        items="frango grelhado · peixe magro · ovos cozidos",
    )),
    ("04_b", tier_slide(
        letter="B", name="carbos integrais", badge_class="tier--b",
        blurb="boa energia sustentada — comer 2-3h antes.",
        items="aveia · arroz integral · batata-doce assada",
    )),
    ("05_a", tier_slide(
        letter="A", name="líquidos & semi-líquidos", badge_class="tier--a",
        blurb="ótimos quando o tempo é curto — porém menos saciedade.",
        items="sports drinks (eletrólitos) · sucos de frutas · frutas com alto teor líquido",
    )),
    ("06_s", tier_slide(
        letter="S", name="carbo simples + leve", badge_class="tier--s",
        blurb="ouro · digestão rápida e energia imediata.",
        items="banana · pão branco com mel · pasta de amendoim no pão · gel de carbo",
    )),
    ("07_close", closing()),
]

# ---------- GYM ----------
CAROUSELS["gym"] = [
    ("01_capa", cover(
        theme="dark",
        kicker="FORÇA · CORREDORES",
        headline_html='você precisa de<br><span class="hl">força</span><br>pra correr mais rápido.',
        lead="correr melhora endurance — não constrói força máxima. e velocidade é força.",
        hero_img=None,
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_track.jpg"
    )),
    ("02_frustracao", content(
        theme="dark",
        num="01",
        title="por que corredores se frustram?",
        body_html=(
            '<p class="body-mono">a maioria dos corredores <b>desperdiça energia</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">pernas doem, quadris doem, panturrilhas <b>não conseguem estocar e devolver força.</b></p>'
            '<p class="body-mono" style="margin-top:18px;">você está em forma. mas <b>ineficiente.</b></p>'
            '<p class="body-mono" style="margin-top:18px;">treinos de força <b>resolvem isso.</b></p>'
        ),
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_smile.jpg"
    )),
    ("03_impulso", content(
        theme="dark",
        num="02",
        title="o que realmente te faz mais rápido.",
        body_html=(
            '<p class="body-mono">cada passada é um <b>impulso</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">quanto mais forte o impulso, <b>maior a distância no mesmo nível de esforço.</b></p>'
            '<p class="body-mono" style="margin-top:18px;"><b>músculos fracos = corrida fraca.</b></p>'
            '<p class="body-mono" style="margin-top:18px;">não existe distância no mundo que conserte isso.</p>'
        ),
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_run.jpg"
    )),
    ("04_como", content(
        theme="dark",
        num="03",
        title="como usar a academia.",
        body_html=(
            '<p class="body-mono">não treine como bodybuilder.<br>treine como <b>corredor que levanta peso</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">foque em:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>cargas <b>pesadas</b></li>'
            '<li>repetições <b>leves a moderadas</b></li>'
            '<li>recuperação <b>total</b> entre séries</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">o objetivo não é se exaurir. é <b>se sentir mais forte.</b></p>'
        ),
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_woman_run.jpg"
    )),
    ("05_dose", content(
        theme="dark",
        num="04",
        title="a dose mínima eficaz.",
        body_html=(
            '<p class="body-mono"><b>2× por semana</b> já transforma sua corrida.</p>'
            '<p class="body-mono" style="margin-top:18px;">priorize:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>agachamento · deadlift · lunge</li>'
            '<li>elevação de panturrilha · 3×15 lentos</li>'
            '<li>core anti-rotação · prancha lateral</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">não substitui corrida. <b>multiplica</b> ela.</p>'
        ),
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_rain.jpg"
    )),
    ("06_close", closing()),
]

# ---------- SONO ----------
CAROUSELS["sono"] = [
    ("01_capa", cover(
        theme="light",
        kicker="SONO · CORREDOR",
        headline_html='se você corre,<br>não precisa de<br><span class="hl">8 horas</span>.',
        lead="precisa de mais — e principalmente da distribuição correta.",
        hero_img=None,
    )),
    ("02_problema", content(
        theme="light",
        num="01",
        title="o problema da regra das 8h.",
        body_html=(
            '<p class="body-mono">a regra das 8h ignora <b>como o corpo realmente recupera</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">duração é só a <b>capa</b>. o que importa são os ciclos por dentro:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>sono <b>profundo</b> · reparação muscular</li>'
            '<li>sono <b>REM</b> · consolidação motora</li>'
            '<li>sono <b>leve</b> · transição</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">se os ciclos estão ruins, <b>8 horas viram irrelevantes.</b></p>'
        ),
    )),
    ("03_precisa", content(
        theme="light",
        num="02",
        title="o que você realmente precisa.",
        body_html=(
            '<p class="body-mono">atletas de endurance frequentemente <b>precisam mais</b> que a média:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li><b>mais sono profundo</b> · após treinos intensos</li>'
            '<li><b>mais sono REM</b> · após volume técnico</li>'
            '<li><b>mais tempo total</b> · semanas de alto volume</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">a maioria precisa de <b>8.5–9.5h</b> · mas a distribuição importa mais que a duração.</p>'
        ),
    )),
    ("04_otimizar", content(
        theme="light",
        num="03",
        title="como otimizar o sono.",
        body_html=(
            '<p class="body-mono">três alavancas com retorno alto:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li><b>consistência</b> · dormir e acordar no mesmo horário</li>'
            '<li><b>luz da manhã</b> · 10min de sol nas primeiras 2h</li>'
            '<li><b>temperatura</b> · quarto a 18-19°c</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">cafeína até <b>10h</b>. álcool destrói sono profundo. telas <b>30min</b> antes de dormir → fora.</p>'
        ),
    )),
    ("05_sinais", content(
        theme="light",
        num="04",
        title="sinais que seu sono não está bom.",
        body_html=(
            '<ul class="arrow-list" style="margin-top:0;">'
            '<li>fc de repouso <b>elevada</b> de manhã</li>'
            '<li>HRV <b>caindo</b> por dias seguidos</li>'
            '<li>treinos no mesmo pace <b>parecem mais difíceis</b></li>'
            '<li><b>fome anormal</b> · principalmente por carbos</li>'
            '<li><b>humor</b> instável · irritação fácil</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:24px;">esses sinais aparecem <b>antes</b> da queda de performance. ouça-os.</p>'
        ),
    )),
    ("06_close", closing()),
]

# ---------- ZONE ----------
CAROUSELS["zone"] = [
    ("01_capa", cover(
        theme="light",
        kicker="ZONAS · SEM RELÓGIO",
        headline_html='você não precisa<br>de relógio pra<br>saber suas <span class="hl">zonas</span>.',
        lead="seu corpo já te diz tudo. você só não está ouvindo.",
        hero_img=None,
    )),
    ("02_problema", content(
        theme="light",
        num="01",
        title="o problema não é o relógio.",
        body_html=(
            '<p class="body-mono">é você <b>terceirizando</b> a percepção do esforço.</p>'
            '<p class="body-mono" style="margin-top:18px;">quando toda decisão vem do pace ou do bpm:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>você <b>para de observar</b> sua respiração</li>'
            '<li>você <b>ignora</b> a tensão crescente</li>'
            '<li>você reage <b>tarde</b> em vez de ajustar antes</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">quanto mais terceiriza, <b>menos sabe sobre si.</b></p>'
        ),
    )),
    ("03_oque_sao", content(
        theme="light",
        num="02",
        title="o que as zonas realmente são.",
        body_html=(
            '<p class="body-mono">não são apenas números — são <b>níveis de esforço</b> definidos por:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>ritmo da <b>respiração</b></li>'
            '<li><b>tensão</b> muscular</li>'
            '<li><b>foco</b> mental disponível</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">bpm e pace variam de dia pra dia. o que <b>não varia</b> é a leitura do seu corpo — se você souber lê-lo.</p>'
        ),
    )),
    ("04_z1z2", content(
        theme="light",
        num="03",
        title="zonas 1 & 2 · sem esforço.",
        body_html=(
            '<p class="body-mono"><b>z1 (recuperação):</b> conversa fluida · respiração nasal possível · sem tensão.</p>'
            '<p class="body-mono" style="margin-top:14px;"><b>z2 (base aeróbica):</b> conversa em frases longas · respiração tranquila pela boca · pernas leves.</p>'
            '<p class="body-mono" style="margin-top:18px;"><b>se o teste do diálogo passa, você está aqui.</b></p>'
            '<p class="body-mono" style="margin-top:18px;">é onde você deveria estar <b>80% do volume.</b></p>'
        ),
    )),
    ("05_z3z4", content(
        theme="light",
        num="04",
        title="zonas 3 & 4 · esforço crescente.",
        body_html=(
            '<p class="body-mono"><b>z3 (ritmo):</b> frases curtas · respiração mais profunda · pernas presentes.</p>'
            '<p class="body-mono" style="margin-top:14px;"><b>z4 (limiar):</b> palavras isoladas · respiração ofegante · queimação começando.</p>'
            '<p class="body-mono" style="margin-top:18px;">aqui você <b>sustenta</b> com esforço · não <b>resiste</b> ao esforço.</p>'
            '<p class="body-mono" style="margin-top:18px;">essa é a fronteira que separa amador de atleta.</p>'
        ),
    )),
    ("06_z5", content(
        theme="light",
        num="05",
        title="zona 5 · vo2 max.",
        body_html=(
            '<p class="body-mono"><b>z5 (vo2):</b> não fala · respiração no limite · queimação total.</p>'
            '<p class="body-mono" style="margin-top:14px;">você <b>não controla</b> esse esforço — ele te controla.</p>'
            '<p class="body-mono" style="margin-top:18px;">use em séries curtas · 3-5 minutos no máximo · com recuperação total entre.</p>'
            '<p class="body-mono" style="margin-top:18px;">é o esforço que <b>amplia o teto</b> de tudo o que está abaixo.</p>'
        ),
    )),
    ("07_close", closing()),
]

# ---------- PLACA ----------
CAROUSELS["placa"] = [
    ("01_capa", cover(
        theme="dark",
        kicker="PLACA DE CARBONO · ARMADILHA",
        headline_html='velocidade<br><span class="hl">gratuita</span><br>não existe.',
        lead="placas de carbono prometem energia retornada — mas o preço é cobrado depois.",
        hero_img=None,
        bg_img="/Users/pedrowanderleyalmeida/Desktop/Merge/brand/images/villarinho_race.jpg"
    )),
    ("02_obsessao", content(
        theme="light",
        num="01",
        title="a obsessão por velocidade.",
        body_html=(
            '<p class="body-mono">todo iniciante busca <b>tênis mais rápidos</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">placas de carbono · super espumas · retorno de energia na pisada.</p>'
            '<p class="body-mono" style="margin-top:18px;">marcas vendem <b>velocidade gratuita</b>. e funciona.</p>'
            '<p class="body-mono" style="margin-top:18px;">mas velocidade <b>sem fundamento</b> é uma armadilha.</p>'
        ),
    )),
    ("03_preco", content(
        theme="light",
        num="02",
        title="o preço escondido.",
        body_html=(
            '<p class="body-mono">a placa altera <b>como sua passada absorve impacto</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">músculos fracos delegam ao tênis o que <b>deveriam fazer:</b></p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>panturrilha <b>perde</b> capacidade elástica</li>'
            '<li>tendões <b>desadaptam</b> à carga real</li>'
            '<li>fáscia plantar e aquiles <b>sobrecarregam</b></li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">o tênis te <b>empurra</b>. mas só até onde seu corpo aguenta.</p>'
        ),
    )),
    ("04_sinais", content(
        theme="light",
        num="03",
        title="sinais que você está exagerando.",
        body_html=(
            '<ul class="arrow-list" style="margin-top:0;">'
            '<li>dor na <b>fáscia plantar</b> ao acordar</li>'
            '<li>aquiles <b>tenso</b> nos primeiros minutos</li>'
            '<li>panturrilha <b>endurecida</b> após o treino</li>'
            '<li>quadril <b>cansado</b> sem causa óbvia</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:24px;">esses sinais aparecem <b>antes</b> da lesão. ignorar = quebrar.</p>'
        ),
    )),
    ("05_quando", content(
        theme="light",
        num="04",
        title="quando usar carbono.",
        body_html=(
            '<p class="body-mono">a placa não é <b>vilã</b>. é <b>ferramenta</b>.</p>'
            '<p class="body-mono" style="margin-top:18px;">use:</p>'
            '<ul class="arrow-list" style="margin-top:14px;">'
            '<li>em <b>provas</b> e treinos-chave</li>'
            '<li><b>após</b> base de força construída</li>'
            '<li>com <b>revezamento</b> de tênis sem placa</li>'
            '</ul>'
            '<p class="body-mono" style="margin-top:18px;">primeiro <b>seu corpo</b> · depois a tecnologia.<br>nunca o contrário.</p>'
        ),
    )),
    ("06_close", closing()),
]


# ============== RENDERER ==============

def render_carousel(name: str, slides: list[tuple[str, str]]) -> None:
    out_dir = OUT / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = ROOT / ".tmp_posts" / name
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n→ {name} ({len(slides)} slides)")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for idx, (slug, html) in enumerate(slides, start=1):
            tmp_html = tmp_dir / f"{idx:02d}_{slug}.html"
            tmp_html.write_text(html, encoding="utf-8")
            page.goto(f"file://{tmp_html}")
            page.wait_for_load_state("networkidle", timeout=15000)
            out_png = out_dir / f"{idx:02d}_{slug}.png"
            page.screenshot(path=str(out_png), full_page=False, omit_background=False, clip={"x": 0, "y": 0, "width": W, "height": H})
            print(f"  ✓ {out_png.name}")
        browser.close()


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    targets = {only: CAROUSELS[only]} if only else CAROUSELS
    OUT.mkdir(parents=True, exist_ok=True)
    for name, slides in targets.items():
        render_carousel(name, slides)
    print("\n✓ done · output em output/posts/")


if __name__ == "__main__":
    main()
