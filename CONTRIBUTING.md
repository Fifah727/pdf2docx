# Contribuer

1. `pip install -e ".[dev]"`
2. `ruff check .` et `python -m pytest -q` doivent passer.
3. Un défaut de mise en page : ajoutez un PDF **minimal et sans données personnelles** dans `corpus/synth/` (ou décrivez-le dans l'issue), puis un test dans `tests/test_pipeline.py`.
4. Avant de proposer un changement des seuils ou du rendu, lancez `python bench/bench.py corpus/synth` et joignez les scores avant et après.
5. Ne publiez jamais de PDF personnels ou protégés : `corpus/real/` est ignoré par git.

En contribuant, vous acceptez que votre code soit publié sous AGPL-3.0-or-later.
