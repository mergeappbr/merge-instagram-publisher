"""
Distribui PNGs renderizados (output/feed e output/stories) nas pastas semanais
no Desktop, renomeando para o padrão N.png / N.X.png.

Também escreve um espelho em `posts/` dentro do repo (apenas feeds + reels)
pra que o scheduler em Railway leia diretamente do GitHub.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_FEED = ROOT / "output" / "feed"
SRC_STORY = ROOT / "output" / "stories"
SRC_REELS = ROOT / "output" / "reels"
DEST_BASE = Path("/Users/pedrowanderleyalmeida/Desktop/Merge - Posts Semanais")
REPO_MIRROR = ROOT / "posts"  # espelho dentro do repo (commitado)

REELS = {
    "Semana 02 (06-10)": [
        ("reel_sub2", "07_reel_londres_masc.mp4"),
    ],
    "Semana 03 (11-15)": [
        ("reel_vo2", "13_reel_vo2.mp4"),
    ],
    "Semana 08 - Corrida (37-41)": [
        ("reel_zonas", "37_reel_zonas_corrida.mp4"),
    ],
    "Semana 10 - Natacao (47-51)": [
        ("reel_drills", "47_reel_drills_natacao.mp4"),
    ],
    "Semana 11 - Prevencao (52-56)": [
        ("reel_lesoes", "52_reel_lesoes_corrida.mp4"),
    ],
    "Semana 12 - Comparativos (57-61)": [
        ("reel_wearables", "57_reel_wearables.mp4"),
    ],
    "Semana 13 - Fortalecimento (62-66)": [
        ("reel_forca", "62_reel_forca_corredor.mp4"),
    ],
}

WEEKS = {
    "Semana 01 (01-05)": [
        ("01_stat_mercado", "01.png"),
        ("02_quote_manifesto", "02.png"),
        ("03_quiz_triatlo", "03.png"),
        ("04_compare_fragmentado", "04.png"),
        ("05_carousel_cover_modalidades", "05.png"),
    ],
    "Semana 02 (06-10)": [
        ("06_feature_londres_fem", "06.png"),
        ("07_feature_londres_masc", "07.png"),
        ("08_feature_gadget_wearable", "08.png"),
        ("09_feature_nutricao_prova", "09.png"),
        ("10_feature_comunidade_swim", "10.png"),
    ],
    "Semana 03 (11-15)": [
        ("11_stat_bike_brasil", "11.png"),
        ("12_stat_sono", "12.png"),
        ("13_stat_vo2", "13.png"),
        ("14_quiz_pace_10k", "14.png"),
        ("15_quiz_zonas_fc", "15.png"),
    ],
    "Semana 04 (16-20)": [
        ("16_quote_comunidade", "16.png"),
        ("17_quote_ritual", "17.png"),
        ("18_compare_planilha", "18.png"),
        ("19_compare_squad", "19.png"),
        ("20_feature_openwater", "20.png"),
    ],
    "Semana 05 (21-25)": [
        ("21_feature_bike_duo", "21.png"),
        ("22_feature_run", "22.png"),
        ("23_feature_swim_face", "23.png"),
        ("24_feature_smile", "24.png"),
        ("25_feature_recovery", "25.png"),
    ],
    "Semana 06 (26-30 + Story 36)": [
        ("26_mockup_feed", "26.png"),
        ("27_mockup_activities", "27.png"),
        ("28_mockup_workouts", "28.png"),
        ("29_mockup_zones", "29.png"),
        ("30_mockup_metrics", "30.png"),
        ("36_intro_spoilers", "36.png"),
    ],
    "Semana 07 (31-35)": [
        ("31_mockup_chat", "31.png"),
        ("32_feature_race", "32.png"),
        ("33_feature_rain", "33.png"),
        ("34_feature_duo", "34.png"),
        ("35_feature_mtb", "35.png"),
    ],
    "Semana 08 - Corrida (37-41)": [
        ("37_zonas_corrida", "37.png"),
        ("37_2_zonas_z1", "37.2.png"),
        ("37_3_zonas_z2", "37.3.png"),
        ("37_4_zonas_z3", "37.4.png"),
        ("37_5_zonas_z4", "37.5.png"),
        ("37_6_zonas_z5", "37.6.png"),
        ("38_rpe", "38.png"),
        ("39_cadencia_corrida", "39.png"),
        ("40_long_run", "40.png"),
        ("41_calendario_corrida", "41.png"),
    ],
    "Semana 09 - Bike (42-46)": [
        ("42_ftp", "42.png"),
        ("43_zonas_potencia", "43.png"),
        ("44_cadencia_bike", "44.png"),
        ("45_mtb_vs_road", "45.png"),
        ("46_calendario_bike", "46.png"),
    ],
    "Semana 10 - Natacao (47-51)": [
        ("47_drills_natacao", "47.png"),
        ("47_2_drill_catchup", "47.2.png"),
        ("47_3_drill_fingertip", "47.3.png"),
        ("47_4_drill_sculling", "47.4.png"),
        ("47_5_drill_six_kick", "47.5.png"),
        ("48_swolf", "48.png"),
        ("49_pace_100m", "49.png"),
        ("50_respiracao_bilateral", "50.png"),
        ("51_aguas_abertas", "51.png"),
    ],
    "Semana 11 - Prevencao (52-56)": [
        ("52_lesoes_corrida", "52.png"),
        ("52_2_lesoes_canelite", "52.2.png"),
        ("52_3_lesoes_fascite", "52.3.png"),
        ("52_4_lesoes_aquiles", "52.4.png"),
        ("52_5_lesoes_joelho", "52.5.png"),
        ("52_6_lesoes_it_band", "52.6.png"),
        ("53_bike_fit", "53.png"),
        ("54_ombro_nadador", "54.png"),
        ("55_periodizacao", "55.png"),
        ("56_recovery", "56.png"),
    ],
    "Semana 12 - Comparativos (57-61)": [
        ("57_wearables", "57.png"),
        ("57_2_garmin_fr965", "57.2.png"),
        ("57_3_coros_pace3", "57.3.png"),
        ("57_4_polar_vantage", "57.4.png"),
        ("57_5_wearables_veredito", "57.5.png"),
        ("58_daily_trainers", "58.png"),
        ("58_2_pegasus41", "58.2.png"),
        ("58_3_novablast5", "58.3.png"),
        ("58_4_endorphin_speed4", "58.4.png"),
        ("58_5_rebel_v5", "58.5.png"),
        ("59_super_shoes", "59.png"),
        ("59_2_vaporfly3", "59.2.png"),
        ("59_3_adios_pro4", "59.3.png"),
        ("59_4_endorphin_pro4", "59.4.png"),
        ("59_5_cielo_x1", "59.5.png"),
        ("60_gps_bike", "60.png"),
        ("60_2_karoo3", "60.2.png"),
        ("60_3_edge1050", "60.3.png"),
        ("60_4_bolt_v2", "60.4.png"),
        ("60_5_roam_v2", "60.5.png"),
        ("61_wetsuit", "61.png"),
        ("61_2_roka_maverick", "61.2.png"),
        ("61_3_orca_apex", "61.3.png"),
        ("61_4_zone3_vanquish", "61.4.png"),
        ("61_5_huub_brownlee", "61.5.png"),
    ],
    "Semana 13 - Fortalecimento (62-66)": [
        ("62_stat_forca_lesao", "62.png"),
        ("63_feature_deadlift", "63.png"),
        ("64_quiz_exercicio_corredor", "64.png"),
        ("65_compare_so_corrida_vs_forca", "65.png"),
        ("66_quote_forca_seguro", "66.png"),
    ],
    "Semana 14 - Nutricao (67-71)": [
        ("67_stat_carbo_amador", "67.png"),
        ("68_feature_intra_prova", "68.png"),
        ("69_quiz_carbo_hora", "69.png"),
        ("70_compare_pre_intra_pos", "70.png"),
        ("71_quote_pilar_invisivel", "71.png"),
    ],
}


def main():
    total = 0
    missing = []
    for week, files in WEEKS.items():
        feed_dir = DEST_BASE / week / "feed"
        story_dir = DEST_BASE / week / "stories"
        mirror_feed = REPO_MIRROR / week / "feed"
        feed_dir.mkdir(parents=True, exist_ok=True)
        story_dir.mkdir(parents=True, exist_ok=True)
        mirror_feed.mkdir(parents=True, exist_ok=True)

        for src_id, dest_name in files:
            f_src = SRC_FEED / f"{src_id}.png"
            s_src = SRC_STORY / f"{src_id}.png"

            if f_src.exists():
                shutil.copy2(f_src, feed_dir / dest_name)
                shutil.copy2(f_src, mirror_feed / dest_name)
                total += 1
            else:
                missing.append(str(f_src))
            if s_src.exists():
                shutil.copy2(s_src, story_dir / dest_name)
                total += 1
            else:
                missing.append(str(s_src))

    reels_total = 0
    for week, files in REELS.items():
        reels_dir = DEST_BASE / week / "reels"
        reels_dir.mkdir(parents=True, exist_ok=True)
        for src_id, dest_name in files:
            r_src = SRC_REELS / f"{src_id}.mp4"
            if r_src.exists():
                shutil.copy2(r_src, reels_dir / dest_name)
                reels_total += 1
            else:
                missing.append(str(r_src))

    print(f"Copiados: {total} PNGs · {reels_total} reels (Desktop + posts/ mirror)")
    if missing:
        print("FALTANDO:")
        for m in missing:
            print(f"  - {m}")


if __name__ == "__main__":
    main()
