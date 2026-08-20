# Archive locale conservatoire de Project Paeonia

Ce petit outil crée une copie **locale et interne** du site Project Paeonia, à partir de
`https://www.paeon.de/name/index.html`. Il ne republie rien et ne transforme pas le contenu
éditorial : seules les références vers des fichiers effectivement récupérés sont rendues
relatives afin de permettre la consultation hors ligne.

## Stratégie retenue après inspection

L'environnement de développement n'a pas pu joindre `paeon.de` (le proxy a répondu HTTP
403, y compris pour `robots.txt`). Il n'a donc pas été possible de valider ici le balisage
réel ni de produire honnêtement une archive initiale. Le site indiqué étant une collection
de pages HTML, la stratégie prudente retenue est un parcours en largeur depuis la page
d'index, piloté par les liens découverts, plutôt qu'une supposition sur les noms de fiches.

Le crawler :

- accepte exclusivement `paeon.de` et `www.paeon.de`, en HTTP ou HTTPS ;
- découvre les pages et ressources dans les attributs HTML usuels (`href`, `src`,
  `srcset`, images, feuilles de style, scripts, cadres et objets), ainsi que les `url(...)`
  des CSS ;
- attend par défaut une seconde entre deux requêtes, réessaie les erreurs réseau et
  télécharge chaque URL normalisée une seule fois ;
- conserve l'arborescence URL sous `archive/<hôte>/...`; les URL avec paramètres reçoivent
  un suffixe stable pour éviter les collisions ;
- réécrit uniquement les références dont la cible a réellement été téléchargée. Les liens
  externes restent tels quels et ne sont jamais suivis.

Avant une collecte institutionnelle, il est recommandé de vérifier les conditions
d'utilisation et `robots.txt` depuis un réseau ayant accès au site, puis d'annoncer la
collecte au responsable du site. Les droits sur le contenu restent ceux de leurs titulaires.

## Installation

Python 3.10 ou plus récent est recommandé :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Lancer l'archivage

Depuis la racine du projet :

```bash
python archive.py crawl
```

La commande reprend une collecte complète dans `archive/`, écrit
`archive_manifest.csv`, adapte les liens à la fin, puis exécute la vérification. Pour être
encore plus courtois envers le serveur :

```bash
python archive.py crawl --delay 2
```

Les paramètres `--timeout` et `--retries` règlent la gestion des pannes. Relancer la
commande remplace les mêmes chemins locaux ; les doublons sont évités au cours de chaque
exécution. Il vaut mieux archiver l'ancien dossier avant une nouvelle collecte si l'on veut
conserver plusieurs états datés.

## Consulter sans connexion Internet

La page d'entrée se trouve normalement ici :

```text
archive/www.paeon.de/name/index.html
```

On peut l'ouvrir directement dans un navigateur (`Fichier` → `Ouvrir`) ou servir le dossier
localement, ce qui est souvent plus compatible avec les règles de sécurité des navigateurs :

```bash
python -m http.server 8000 --directory archive
```

Puis ouvrir `http://localhost:8000/www.paeon.de/name/index.html`. Ce serveur n'a besoin
d'aucune connexion Internet ; l'arrêter avec `Ctrl+C`.

## Vérification et éléments manquants

Cette commande ne contacte jamais le site :

```bash
python archive.py verify
```

Elle contrôle que chaque téléchargement réussi du manifeste existe, signale chaque statut
`failed`, détecte les liens internes `paeon.de` qui n'ont pas été adaptés et teste les cibles
locales HTML. Elle retourne un code non nul si l'archive est incomplète.

`archive_manifest.csv` est la source de suivi : une ligne par URL, avec l'URL originale, le
chemin local, le type MIME, la date UTC de récupération et le statut. Les pages HTTP 404,
les délais dépassés et autres erreurs restent donc explicitement visibles. Une ressource
externe peut rester mentionnée dans le HTML original, mais elle n'est ni téléchargée ni
considérée comme disponible hors ligne, conformément au périmètre demandé.

> Note : l'archive versionnée est volontairement vide (`archive/.gitkeep`). Elle sera
> alimentée au premier lancement depuis un réseau autorisé à accéder à `paeon.de`.
