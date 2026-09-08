# DQ Compass

Dispositif de contrôle qualité des données, générique et piloté par catalogue.
Datathon MBA-ESG / SG GSC — septembre 2026.

Le moteur ne connaît aucun nom de colonne, aucun seuil, aucun dataset. Tout
vient du catalogue. Ajouter un contrôle ou un dataset ne demande jamais une
ligne de code.

---

## Démarrer

### Depuis un clone

```bash
git clone https://github.com/Yassine-Kamali/dq-compass.git
cd dq-compass
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt          # macOS / Linux

./.venv/Scripts/python.exe engine/prepare_bis.py     # extrait le zip et prépare les données
./.venv/Scripts/python.exe tests/test_dq.py          # 60 tests, doit finir à 60/60
```

Seul le zip source (4,8 Mo) est versionné. `prepare_bis.py` l'extrait au premier
lancement et régénère les 112 Mo de données préparées en une quinzaine de
secondes, avec des empreintes SHA-256 identiques d'une machine à l'autre.

### Au quotidien

```bash
./.venv/Scripts/python.exe -m streamlit run ui/app.py       # interface — le chemin normal
./.venv/Scripts/python.exe tests/test_dq.py                 # 60 tests

# en ligne de commande : un fichier à la fois, le contrat est deviné
./.venv/Scripts/python.exe reporting/excel_report.py data/prepared/bis_turnover.csv
./.venv/Scripts/python.exe engine/dq_engine.py data/ref/ref_devises.csv
```

Un venv est déjà installé à la racine. Sur une autre machine :
`python -m venv .venv && .venv/Scripts/pip install -r requirements.txt`.

> Le Python global de cette machine a un pandas 2.1.4 incompatible avec son
> numpy 2.5.2 : tout import de pandas y échoue. **Utilisez toujours
> `./.venv/Scripts/python.exe`**, jamais `python` seul.

---

## Architecture

Quatre composants, une seule direction de dépendance : rien ne remonte vers le
catalogue.

| # | Composant | Fichier | Rôle |
|---|---|---|---|
| 1 | Catalogue | `catalogue/store.json` | **DÉFINIT** les contrôles — source de vérité |
| 2 | Moteur | `engine/dq_engine.py` | **EXÉCUTE**, quel que soit le dataset |
| 3 | Reporting | `reporting/excel_report.py` | **RESTITUE** le classeur métier |
| 4 | Audit | `evidence/<run_id>/` | **PROUVE** l'exécution, rejouable |

L'interface (`ui/app.py`) pilote le catalogue ; elle ne contient aucune logique
de contrôle.

### Un run = un fichier

On dépose **un seul fichier**. Le moteur reconnaît de quoi il s'agit en
comparant ses colonnes aux contrats déclarés, puis applique les règles qui
concernent ce contrat. Les référentiels ne sont pas des fichiers à fournir :
le moteur va les chercher lui-même quand un contrôle d'intégrité en a besoin,
et les empreinte comme toute autre source.

Un contrat décrit une **structure**, pas un fichier : l'extrait de
démonstration et le fichier complet respectent le même contrat `bis_turnover`.

### Aucun jargon dans l'interface

Un utilisateur qui n'est pas data ne voit jamais `MATCHES_REGEX` ni
`{"column": "montant"}`. Il lit « Respecter un format précis » et « La colonne
Montant doit respecter le format ^[0-9]+$ ». Ce vocabulaire est porté par le
**catalogue** (`libelle_metier`, `question_metier`, `phrase`,
`params_libelles`), pas par le code de l'interface : ajouter un template suffit
à le rendre pilotable en langage clair.

L'éditeur guide en quatre étapes — que vérifier, sur quoi, que faire en cas
d'écart, comment nommer la règle — et refuse d'enregistrer tant que le nom, la
raison d'être et l'action de remédiation manquent. Les tolérances sont
proposées en clair plutôt qu'en champ numérique libre.

**Excel est une sortie, pas une entrée.** Le métier consomme le classeur ; il
édite les règles par l'interface. Le store JSON est la source de vérité, ce qui
évite les conflits d'écriture d'un classeur partagé.

### Le catalogue à deux niveaux

- **Template** — générique, indépendant de tout dataset. 12 implémentés une
  seule fois dans le moteur : `NOT_NULL`, `MATCHES_REGEX`, `IN_DOMAIN`,
  `RANGE`, `UNIQUE_KEY`, `FOREIGN_KEY`, `FIELD_EQUALS`, `DATE_ORDER`,
  `FRESHNESS`, `SUM_RECONCILIATION`, `COUNT_RECONCILIATION`,
  `CUSTOM_EXPRESSION`.
- **Instance de contrôle** — un template relié à un périmètre, des paramètres,
  un seuil, un propriétaire, une sévérité. C'est une ligne du catalogue.

### Ce qui rend une règle universelle

Une règle cible soit une **colonne** (`{"column": "montant"}`), soit un **rôle**
(`{"role": "identifiant"}`). Avec un rôle et un périmètre `*`, une seule ligne
de catalogue s'applique à tous les datasets déclarés — y compris ceux qui
n'existent pas encore. Déclarer le contrat d'un nouveau dataset suffit à lui
appliquer tous les contrôles transverses.

### Gouvernance appliquée par l'outil

- **Aucune suppression.** `delete_control()` lève une exception ; l'interface
  propose Suspendre et Déprécier. L'audit exige de rejouer les runs historiques.
- **Versionnement automatique.** Toute modification d'un champ exécutable
  (template, paramètres, périmètre, seuil, statut) incrémente la version et
  met à jour `effective_from`.
- **Journal d'audit.** Qui, quand, quel champ, valeur avant → après, et pour
  quel motif. Visible dans l'interface et dans l'onglet `JOURNAL` du classeur.

---

## Le classeur de sortie

`reporting/sorties/DQ_Rapport_<run_id>.xlsx`, six onglets, autoportant :

| Onglet | Contenu |
|---|---|
| `SYNTHESE` | **Nombre de contrôles en échec en premier et en grand**, puis le détail : une ligne par contrôle, sa traduction en français, feux tricolores |
| `EXCEPTIONS` | Détail ligne à ligne, filtrable par règle et sévérité |
| `COUVERTURE` | Dimensions × datasets, trous de couverture, répartition par sévérité |
| `EVIDENCE` | Run ID, empreintes SHA-256 des sources et du catalogue, rejets |
| `CATALOGUE_EXECUTE` | Copie figée des règles telles qu'exécutées |
| `JOURNAL` | Journal des modifications du catalogue |

---

## Résultats sur les données réelles

BIS Triennial Survey, OTC derivatives turnover — 77 992 lignes brutes,
**425 371 observations** après passage en format long.

Run complet : **6,7 s**, rapport Excel et chargement des référentiels inclus. 15 contrôles applicables au
contrat `bis_turnover`, 6 dimensions, 13 PASS / 2 FAIL.

Les deux échecs sont de vrais constats métier :

- **DQ03** — 2 turnovers notionnels négatifs sur 425 371. Escalade Market Data Office.
- **DQ09** — 32 lignes portant `CLS` en devise jambe 2. `CLS` est un mécanisme
  de règlement, pas une monnaie : arbitrage Data Steward, enrichir le
  référentiel ou rejeter.

### Deux calibrages qui valent d'être racontés

Le premier run avait produit deux échecs pour de mauvaises raisons. Les
corrections sont tracées dans le journal (`engine/apply_calibration.py`) :

**DQ05 — 22 063 faux positifs.** Le format `^[A-Z]{3}$` rejetait `TO1`, qui est
le code agrégat officiel BIS. 100 % des exceptions venaient de la règle, pas de
la donnée. Le format valide doit admettre l'agrégat.

**DQ13 — un contrôle de réconciliation qui mesurait la mauvaise chose.** Sur
1 024 agrégats, 781 ont une somme des pays *inférieure* au total mondial, 243
sont égaux, **aucun ne le dépasse**. L'écart est structurel — le total BIS
inclut des juridictions non publiées individuellement. Le contrôle a été scindé :

- `DQ13` (Reconciliation, Critical) — la somme des pays ne doit **jamais
  dépasser** le total. Un dépassement serait un double comptage. → 0 exception.
- `DQ16` (Completeness, Medium) — le détail publié doit couvrir ≥ 95 % du
  total. → 14 agrégats sous le seuil, couverture médiane 99,90 %.

Même arithmétique, deux dimensions, deux propriétaires, deux remédiations.

---

## Séquence de démonstration

1. **Déposer un fichier** (90 s) — écran *Contrôler un fichier* → déposer
   `bis_turnover_demo.csv`. Le système annonce « Fichier reconnu : BIS
   Triennial Survey, 100 % des colonnes attendues », liste les règles
   applicables, puis lance. **Le nombre de contrôles en échec s'affiche en
   premier, en grand**, suivi de qui doit faire quoi.
2. **Le classeur** (90 s) — télécharger le rapport. Même hiérarchie : les
   échecs d'abord, chaque règle traduite en français, puis exceptions,
   couverture, preuves. C'est ce que le métier reçoit.
3. **Créer une règle sans être data** (2 min) — écran *Règles de contrôle* →
   *Créer une règle*. On choisit « Ne jamais être vide », pas `NOT_NULL`. Le
   récapitulatif écrit la phrase en français. Le bouton reste bloqué tant que
   l'action de remédiation manque : on ne peut pas produire une règle orpheline.
4. **Le validateur** (1 min) — choisir « Rester dans des bornes chiffrées » sur
   `DER_BASIS`, une colonne texte. Refus immédiat, message clair, avant
   exécution. Une règle malformée ne casse jamais un contrôle.
5. **La suppression refusée** (30 s) — cliquer Supprimer. L'outil explique que
   les rapports déjà produits doivent rester explicables, et oriente vers
   *Mettre en pause*.
6. **Le fichier inconnu** (90 s) — déposer un fichier non décrit : le système
   dit qu'il ne le reconnaît pas et renvoie vers *Fichiers connus*. Y décrire
   ses colonnes, redéposer : les règles transverses (`*` et par rôle)
   s'appliquent seules. Zéro règle écrite, zéro code modifié.
7. **La reproductibilité** (30 s) — relancer le même fichier. Empreintes
   identiques, résultats identiques, `run_id` différent.

---

## Arborescence

```
catalogue/store.json        source de vérité (templates, contrats, contrôles, journal)
engine/store.py             CRUD + versionnement + journal d'audit
engine/dq_engine.py         validateur, 12 exécuteurs, runner, evidence pack
engine/prepare_bis.py       préparation des données et des référentiels
engine/bootstrap_store.py   initialisation du catalogue
engine/apply_calibration.py calibrages tracés du premier run
engine/add_libelles_metier.py couche de langage métier des templates
engine/fusion_contrats.py   fusion des contrats redondants
data/entrees/               fichiers déposés via l'interface
reporting/excel_report.py   classeur six onglets
ui/app.py                   interface Streamlit, quatre écrans
tests/test_dq.py            60 tests, sans dépendance externe
data/raw/                   source BIS d'origine
data/prepared/              format long + extrait de démonstration
data/ref/                   référentiels devises et pays
evidence/<run_id>/          preuves d'exécution
reporting/sorties/          classeurs générés
```
