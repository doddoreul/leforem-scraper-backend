# leforem-scraper-backend

Scraper Le Forem avec export JSON pour le frontend.

## Architecture

```
leforem.be
    │
    ▼
scraper (python/scraper.py)
    │
    ▼
SQLite (data/leforem.db) — source de vérité
    │
    ▼
raw.json — miroir des offres pour le frontend
```

## Principes

- **SQLite reste la source de vérité** : le scraper écrit dans SQLite, jamais
  directement dans `raw.json`.
- **`raw.json` est un miroir** : il est généré depuis SQLite après chaque
  scraping, jamais l'inverse.
- **Le frontend est statique** : il lit `raw.json` et ne dépend ni de Python,
  ni de SQLite, ni d'une API locale.

## Installation

```bash
python -m pip install -r requirements.txt
```

## Utilisation

### Lancer le scraper

```bash
python -m python.scraper --occupation-guid GUID --location-guid GUID
```

### Générer raw.json

```bash
python -m python.export
```

Options :
- `--check` : rapporte sans écrire
- `--min-ratio 0.5` : chute d'offres refusée au-delà (défaut 0.5)
- `--out chemin.json` : chemin alternatif

## Structure

```
leforem-scraper-backend/
├── python/
│   ├── scraper.py      # scraping Le Forem
│   ├── export.py       # export SQLite → raw.json
│   ├── core.py         # logique pure (hash, dates, états)
│   ├── config.py       # chemins et constantes
│   ├── server.py       # serveur local (développement)
│   ├── employers.py    # index des employeurs
│   ├── salary.py       # analyse des salaires
│   └── storage/        # couche d'accès SQLite
├── data/               # données SQLite (leforem.db)
├── raw.json            # miroir des offres pour le frontend
├── tests/              # suite de tests
└── requirements.txt    # dépendances Python
```

## Tests

```bash
python -m unittest discover -s tests
```

## Publication

Après un scraping :

```bash
python -m python.export
git add raw.json
git commit -m "Mise à jour des offres"
git push
```

Ensuite, copier `raw.json` dans le dépôt frontend et pousser.
