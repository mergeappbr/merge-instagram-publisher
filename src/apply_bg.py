"""
Apply background photos to briefs 37-61 to hit 75/25 photo/black ratio.
Updates JSON briefs in-place adding BG_IMAGE / OVERLAY / BG_POSITION vars.
"""
import json
from pathlib import Path

BRIEFS = Path(__file__).resolve().parent.parent / "content" / "briefs"

# 15 briefs that stay BLACK (no BG_IMAGE) — 25%
BLACK_LIST = {
    "57_2_garmin_fr965",
    "57_3_coros_pace3",
    "57_4_polar_vantage",
    "37_2_zonas_z1",
    "37_3_zonas_z2",
    "37_4_zonas_z3",
    "37_5_zonas_z4",
    "37_6_zonas_z5",
    "47_2_drill_catchup",
    "48_swolf",
    "52_2_lesoes_canelite",
    "52_3_lesoes_fascite",
    "52_4_lesoes_aquiles",
    "52_5_lesoes_joelho",
    "52_6_lesoes_it_band",
}

# Photo mapping for the 44 photo briefs
# (BG_IMAGE, OVERLAY, BG_POSITION)
PHOTO_MAP = {
    # Carrossel zonas corrida (capa)
    "37_zonas_corrida": ("villarinho_run.jpg", "bottom", "center 35%"),
    # RPE
    "38_rpe": ("villarinho_track.jpg", "bottom", "center 40%"),
    # Cadência corrida
    "39_cadencia_corrida": ("villarinho_woman_run.jpg", "bottom", "center 30%"),
    # Long run, calendário corrida — já têm bg (mantém)
    # FTP
    "42_ftp": ("villarinho_bike.jpg", "bottom", "center 40%"),
    # Zonas potência
    "43_zonas_potencia": ("villarinho_bike_motion.jpg", "bottom", "center 40%"),
    # Cadência bike
    "44_cadencia_bike": ("villarinho_bike_pair.jpg", "bottom", "center 35%"),
    # MTB x Road
    "45_mtb_vs_road": ("villarinho_mtb_trail.jpg", "bottom", "center 35%"),
    # Drills natação capa
    "47_drills_natacao": ("villarinho_swim.jpg", "bottom", "center 40%"),
    # Drills 2-4 (drill 1 fica preto)
    "47_3_drill_fingertip": ("villarinho_swim_face.jpg", "bottom", "center 40%"),
    "47_4_drill_sculling": ("villarinho_swim.jpg", "bottom", "center 35%"),
    "47_5_drill_six_kick": ("villarinho_swim_face.jpg", "bottom", "center 35%"),
    # Pace 100m
    "49_pace_100m": ("villarinho_swim.jpg", "bottom", "center 40%"),
    # Respiração já tem bg (mantém)
    # Águas abertas já tem bg (mantém)
    # Lesões capa (carrossel)
    "52_lesoes_corrida": ("villarinho_woman_run.jpg", "bottom", "center 35%"),
    # Bike fit
    "53_bike_fit": ("villarinho_bike.jpg", "bottom", "center 40%"),
    # Ombro nadador já tem bg (mantém)
    # Periodização
    "55_periodizacao": ("villarinho_smile.jpg", "bottom", "center 35%"),
    # Recovery
    "56_recovery": ("villarinho_rain_bw.jpg", "bottom", "center 35%"),
    # Wearables capa
    "57_wearables": ("villarinho_watch.jpg", "bottom", "center 40%"),
    # Wearables veredito
    "57_5_wearables_veredito": ("gadget.jpg", "bottom", "center 40%"),
    # Daily trainers capa
    "58_daily_trainers": ("villarinho_run.jpg", "bottom", "center 40%"),
    # Daily trainers slides
    "58_2_pegasus41": ("villarinho_track.jpg", "bottom", "center 40%"),
    "58_3_novablast5": ("villarinho_run.jpg", "bottom", "center 40%"),
    "58_4_endorphin_speed4": ("villarinho_woman_run.jpg", "bottom", "center 35%"),
    "58_5_rebel_v4": ("villarinho_track.jpg", "bottom", "center 40%"),
    # Super shoes capa
    "59_super_shoes": ("villarinho_race.jpg", "bottom", "center 35%"),
    "59_2_vaporfly3": ("marathon.jpg", "bottom", "center 40%"),
    "59_3_adios_pro4": ("villarinho_race.jpg", "bottom", "center 40%"),
    "59_4_endorphin_pro4": ("marathon.jpg", "bottom", "center 35%"),
    "59_5_cielo_x1": ("villarinho_race.jpg", "bottom", "center 40%"),
    # GPS bike capa + slides
    "60_gps_bike": ("villarinho_bike_motion.jpg", "bottom", "center 40%"),
    "60_2_karoo3": ("villarinho_bike.jpg", "bottom", "center 40%"),
    "60_3_edge1050": ("villarinho_bike_motion.jpg", "bottom", "center 40%"),
    "60_4_bolt_v2": ("villarinho_bike_pair.jpg", "bottom", "center 35%"),
    "60_5_roam_v2": ("villarinho_mtb_trail.jpg", "bottom", "center 35%"),
    # Wetsuit capa + slides
    "61_wetsuit": ("villarinho_openwater.jpg", "bottom", "center 40%"),
    "61_2_roka_maverick": ("villarinho_openwater.jpg", "bottom", "center 35%"),
    "61_3_orca_apex": ("villarinho_openwater.jpg", "bottom", "center 40%"),
    "61_4_zone3_vanquish": ("villarinho_openwater.jpg", "bottom", "center 45%"),
    "61_5_huub_brownlee": ("villarinho_openwater.jpg", "bottom", "center 40%"),
}


def main():
    updated = 0
    cleared = 0
    skipped = 0
    for path in sorted(BRIEFS.glob("*.json")):
        bid = path.stem
        # Only process briefs in 37-61 range
        first = bid.split("_")[0]
        if not first.isdigit():
            continue
        n = int(first)
        if n < 37 or n > 61:
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        v = data.setdefault("vars", {})

        if bid in BLACK_LIST:
            # Strip any BG vars to ensure pure black
            removed = False
            for k in ("BG_IMAGE", "OVERLAY", "BG_POSITION"):
                if k in v:
                    del v[k]
                    removed = True
            if removed:
                cleared += 1
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                skipped += 1
            print(f"BLACK  {bid}")
            continue

        if bid in PHOTO_MAP:
            bg, overlay, pos = PHOTO_MAP[bid]
            v["BG_IMAGE"] = bg
            v["OVERLAY"] = overlay
            v["BG_POSITION"] = pos
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            updated += 1
            print(f"PHOTO  {bid}  ← {bg}")
            continue

        # Already has BG (existing feature posts)
        if v.get("BG_IMAGE"):
            print(f"KEEP   {bid}  (existing BG: {v['BG_IMAGE']})")
            skipped += 1
            continue

        print(f"WARN   {bid}  — sem mapeamento")

    print(f"\nUpdated: {updated} | Cleared: {cleared} | Skipped: {skipped}")


if __name__ == "__main__":
    main()
