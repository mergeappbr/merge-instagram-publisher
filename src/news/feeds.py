"""
Configuração das fontes RSS monitoradas pelo news watcher.

Pesos:
  weight_relevance: 0-1 (quanto a fonte tende a ser aderente ao nicho)
  category: br | wellness_global | endurance | science | event_official
  modalities: lista de modalidades-chave que a fonte cobre

Foco endurance: UTMB, corrida (road/trail), ciclismo (road/MTB), Hyrox, natação.
"""
from __future__ import annotations

FEEDS: list[dict] = [
    # ---------------- BR ----------------
    {
        "name": "OFitFeed",
        "url": "https://www.ofitfeed.com/feed",
        "category": "br",
        "modalities": ["corrida", "ciclismo", "natacao", "triatlo", "wellness"],
        "weight_relevance": 0.85,
    },
    {
        "name": "NeoFeed Wellness",
        "url": "https://neofeed.com.br/categoria/saude-bem-estar/feed/",
        "category": "br",
        "modalities": ["wellness"],
        "weight_relevance": 0.7,
    },
    {
        "name": "Globo Esporte · Atletismo",
        "url": "https://ge.globo.com/dynamo/atletismo/rss2.xml",
        "category": "br",
        "modalities": ["corrida", "atletismo"],
        "weight_relevance": 0.75,
    },
    {
        "name": "Globo Esporte · Eu Atleta",
        "url": "https://ge.globo.com/dynamo/eu-atleta/rss2.xml",
        "category": "br",
        "modalities": ["corrida", "ciclismo", "natacao", "triatlo"],
        "weight_relevance": 0.8,
    },

    # ---------------- Wellness / longevidade global ----------------
    {
        "name": "Outside Online",
        "url": "https://www.outsideonline.com/feed/",
        "category": "wellness_global",
        "modalities": ["corrida", "trail", "ciclismo", "wellness"],
        "weight_relevance": 0.8,
    },
    {
        "name": "MindBodyGreen",
        "url": "https://www.mindbodygreen.com/rss",
        "category": "wellness_global",
        "modalities": ["wellness"],
        "weight_relevance": 0.55,
    },
    {
        "name": "Well+Good",
        "url": "https://www.wellandgood.com/feed/",
        "category": "wellness_global",
        "modalities": ["wellness"],
        "weight_relevance": 0.5,
    },
    {
        "name": "Peter Attia · The Drive",
        "url": "https://peterattiamd.com/feed/",
        "category": "wellness_global",
        "modalities": ["longevidade", "performance"],
        "weight_relevance": 0.85,
    },
    {
        "name": "Huberman Lab",
        "url": "https://hubermanlab.com/feed/",
        "category": "wellness_global",
        "modalities": ["sleep", "performance", "wellness"],
        "weight_relevance": 0.8,
    },
    {
        "name": "Levels Health",
        "url": "https://www.levelshealth.com/blog/feed",
        "category": "wellness_global",
        "modalities": ["nutricao", "metabolismo"],
        "weight_relevance": 0.7,
    },

    # ---------------- Esportivos globais (modalidades-chave) ----------------
    {
        "name": "Runner's World",
        "url": "https://www.runnersworld.com/rss/all.xml/",
        "category": "endurance",
        "modalities": ["corrida"],
        "weight_relevance": 0.85,
    },
    {
        "name": "Trail Runner Magazine",
        "url": "https://www.trailrunnermag.com/feed/",
        "category": "endurance",
        "modalities": ["trail", "utmb"],
        "weight_relevance": 0.9,
    },
    {
        "name": "Triathlete",
        "url": "https://www.triathlete.com/feed/",
        "category": "endurance",
        "modalities": ["triatlo", "natacao", "ciclismo", "corrida"],
        "weight_relevance": 0.85,
    },
    {
        "name": "Slowtwitch",
        "url": "https://www.slowtwitch.com/rss.xml",
        "category": "endurance",
        "modalities": ["triatlo"],
        "weight_relevance": 0.8,
    },
    {
        "name": "Velo (cycling)",
        "url": "https://velo.outsideonline.com/feed/",
        "category": "endurance",
        "modalities": ["ciclismo"],
        "weight_relevance": 0.8,
    },
    {
        "name": "Bicycling",
        "url": "https://www.bicycling.com/rss/all.xml/",
        "category": "endurance",
        "modalities": ["ciclismo"],
        "weight_relevance": 0.75,
    },
    {
        "name": "SwimSwam",
        "url": "https://swimswam.com/feed/",
        "category": "endurance",
        "modalities": ["natacao"],
        "weight_relevance": 0.85,
    },
    {
        "name": "Stronger by Science",
        "url": "https://www.strongerbyscience.com/feed/",
        "category": "science",
        "modalities": ["forca", "performance"],
        "weight_relevance": 0.8,
    },
    {
        "name": "UTMB World Series",
        "url": "https://utmb.world/en/news/rss",
        "category": "event_official",
        "modalities": ["utmb", "trail"],
        "weight_relevance": 0.95,
    },
    {
        "name": "Hyrox Official",
        "url": "https://hyrox.com/feed/",
        "category": "event_official",
        "modalities": ["hyrox"],
        "weight_relevance": 0.95,
    },

    # ---------------- Ciência ----------------
    {
        "name": "PubMed · endurance/longevity",
        "url": "https://pubmed.ncbi.nlm.nih.gov/rss/search/1HwGSW8gJ8h7G_M0vKPMz5o-2P-VlCJ5JHsnK9C_3VJZ/?limit=20",
        "category": "science",
        "modalities": ["performance", "longevidade"],
        "weight_relevance": 0.7,
    },
    {
        "name": "Examine.com",
        "url": "https://examine.com/feed/",
        "category": "science",
        "modalities": ["suplementacao", "nutricao"],
        "weight_relevance": 0.75,
    },
]
