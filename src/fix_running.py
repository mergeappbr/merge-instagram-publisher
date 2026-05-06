"""
Aplica fotos de corrida nas 6 capas/slides do carrossel 37 (zonas)
e troca a foto do 41 (calendário corrida) de bike pra corrida.
"""
import json
from pathlib import Path

BRIEFS = Path(__file__).resolve().parent.parent / "content" / "briefs"

UPDATES = {
    # 37 capa — fundo de mass running, todo corredor
    "37_zonas_corrida": {
        "BG_IMAGE": "marathon.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 60%",
    },
    # Z1 recuperação ativa — sorriso pós-prova
    "37_2_zonas_z1": {
        "BG_IMAGE": "villarinho_smile.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 30%",
    },
    # Z2 base aeróbica — passada constante
    "37_3_zonas_z2": {
        "BG_IMAGE": "villarinho_woman_run.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 25%",
    },
    # Z3 ritmo — corredor sustentado, chuva
    "37_4_zonas_z3": {
        "BG_IMAGE": "villarinho_rain.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 30%",
    },
    # Z4 limiar — corredor intenso em trilha
    "37_5_zonas_z4": {
        "BG_IMAGE": "villarinho_run.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 40%",
    },
    # Z5 VO2max — pista, esforço máximo
    "37_6_zonas_z5": {
        "BG_IMAGE": "villarinho_track.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 35%",
    },
    # 41 calendário corrida — dois correndo juntos (vibe de prova / pelotão)
    "41_calendario_corrida": {
        "BG_IMAGE": "villarinho_friends.jpg",
        "OVERLAY": "bottom",
        "BG_POSITION": "center 40%",
    },
}


def main():
    for bid, vars_update in UPDATES.items():
        path = BRIEFS / f"{bid}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("vars", {}).update(vars_update)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✓ {bid}  ← {vars_update['BG_IMAGE']}")


if __name__ == "__main__":
    main()
