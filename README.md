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
./.venv/Scripts/python.exe tests/test_dq.py          # 46 tests, doit finir à 46/46
```

Seul le zip source (4,8 Mo) est versionné. `prepare_bis.py` l'extrait au premier
lancement et régénère les 112 Mo de données préparées en une quinzaine de
secondes, avec des empreintes SHA-256 identiques d'une machine à l'autre.

### Au quotidien

```bash
./.venv/Scripts/python.exe engine/prepare_bis.py            # préparer les données (une fois)
./.venv/Scripts/python.exe reporting/excel_report.py        # run + classeur Excel
./.venv/Scripts/python.exe -m streamlit run ui/app.py       # interface
./.venv/Scripts/python.exe tests/test_dq.py                 # 46 tests
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
| `SYNTHESE` | Scorecard : bandeau KPI, une ligne par contrôle, feux tricolores |
| `EXCEPTIONS` | Détail ligne à ligne, filtrable par règle et sévérité |
| `COUVERTURE` | Dimensions × datasets, trous de couverture, répartition par sévérité |
| `EVIDENCE` | Run ID, empreintes SHA-256 des sources et du catalogue, rejets |
| `CATALOGUE_EXECUTE` | Copie figée des règles telles qu'exécutées |
| `JOURNAL` | Journal des modifications du catalogue |

---

## Résultats sur les données réelles

BIS Triennial Survey, OTC derivatives turnover — 77 992 lignes brutes,
**425 371 observations** après passage en format long.

Run complet : **4,6 s**, rapport Excel inclus. 19 contrôles, 6 dimensions,
17 PASS / 2 FAIL.

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

1. **Le classeur** (2 min) — ouvrir un rapport. Scorecard, exceptions, couverture,
   evidence. C'est ce que le métier reçoit.
2. **Modifier une règle** (2 min) — interface → Catalogue → DQ03 → passer le
   seuil à 0,01 % → constater la version qui s'incrémente → Journal → la ligne
   apparaît avec le motif.
3. **Le validateur** (1 min) — créer un contrôle `RANGE` sur `DER_BASIS` (une
   colonne texte). Le moteur refuse avant exécution, avec un message clair.
   Un contrôle malformé ne casse jamais un run.
4. **La suppression refusée** (30 s) — cliquer Supprimer. L'outil explique
   pourquoi l'audit l'interdit et oriente vers Suspendre.
5. **Le dataset inconnu** (90 s) — onglet Datasets → déclarer un contrat de
   colonnes → relancer. Les contrôles transverses (`*` et par rôle) s'appliquent
   seuls. Zéro règle écrite, zéro code modifié.
6. **La reproductibilité** (30 s) — relancer le même run. Empreintes identiques,
   résultats identiques, `run_id` différent.

---

## Arborescence

```
catalogue/store.json        source de vérité (templates, datasets, contrôles, journal)
engine/store.py             CRUD + versionnement + journal d'audit
engine/dq_engine.py         validateur, 12 exécuteurs, runner, evidence pack
engine/prepare_bis.py       préparation des données et des référentiels
engine/bootstrap_store.py   initialisation du catalogue
engine/apply_calibration.py calibrages tracés du premier run
reporting/excel_report.py   classeur six onglets
ui/app.py                   interface Streamlit, quatre écrans
tests/test_dq.py            46 tests, sans dépendance externe
data/raw/                   source BIS d'origine
data/prepared/              format long + extrait de démonstration
data/ref/                   référentiels devises et pays
evidence/<run_id>/          preuves d'exécution
reporting/sorties/          classeurs générés
```
