# DQ Compass

Dispositif de contrôle qualité des données, générique et piloté par catalogue.
Datathon MBA-ESG / SG GSC — septembre 2026.

Le moteur ne connaît aucun nom de colonne, aucun seuil, aucune structure de
fichier. Tout vient du catalogue. **Rien ne se déclare** : on dépose un fichier,
le moteur en déduit la structure et applique les règles dont les colonnes
existent. Ajouter un contrôle ou contrôler un nouveau fichier ne demande jamais
une ligne de code, ni le moindre formulaire de description.

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
./.venv/Scripts/python.exe tests/test_dq.py          # 85 tests, doit finir à 85/85
```

Seul le zip source (4,8 Mo) est versionné. `prepare_bis.py` l'extrait au premier
lancement et régénère les 112 Mo de données préparées en une quinzaine de
secondes, avec des empreintes SHA-256 identiques d'une machine à l'autre.

### Au quotidien

```bash
./.venv/Scripts/python.exe -m streamlit run ui/app.py       # interface — le chemin normal
./.venv/Scripts/python.exe tests/test_dq.py                 # 85 tests

# en ligne de commande : un fichier à la fois, sa structure est déduite
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
| 2 | Moteur | `engine/dq_engine.py` | **EXÉCUTE**, quel que soit le fichier |
| 3 | Reporting | `reporting/excel_report.py` | **RESTITUE** le classeur métier |
| 4 | Audit | `evidence/<run_id>/` | **PROUVE** l'exécution, rejouable |

L'interface (`ui/app.py`) suit ce cycle de vie et en reprend le vocabulaire,
écran par écran. Elle ne contient aucune logique de contrôle.

| Écran | Composant du brief | Rôle |
|---|---|---|
| ① Catalogue de contrôles | *Control Catalogue* | **DEFINES** — les règles, leurs 14 attributs, la couverture des 6 dimensions |
| ② Exécution | *Data Quality Engine* | **EXECUTES** — profil du fichier, contrôles proposés, lancement |
| ③ Restitution | *Reporting Layer* | **REPORTS** — tableau de bord graphique, exceptions, couverture, historique |
| ④ Piste d'audit | *Audit Layer* | **EVIDENCES** — packs de preuves, sign-off, journal |

### Un run = un fichier, rien à déclarer

On dépose **un seul fichier**, n'importe lequel. Le moteur le **profile** à la
lecture (`engine/profiler.py`) : noms de colonnes, types déduits du contenu,
taux de valeurs vides, colonnes sans doublon. Ce profil remplace intégralement
la déclaration manuelle d'un schéma.

Le brief impose deux choses en apparence contradictoires : une couche
« plug-and-play […] configurable with minimal effort » (§2.1, §3.2), et une
« Dataset Reference : version / snapshot / hash of input dataset » dans chaque
evidence pack (annexe B.2). La conciliation tient en une phrase : **le schéma
n'est pas déclaré, il est dérivé** — personne ne le saisit, et il devient la
pièce d'audit qui décrit l'entrée.

Chaque règle active dont la portée couvre le fichier est ensuite évaluée :

| Issue | Signification |
|---|---|
| `PASS` / `FAIL` | La règle s'applique et rend un verdict |
| `NON_APPLICABLE` | La règle est active mais ses colonnes n'existent pas ici — ni échec, ni erreur, avec le motif exact |
| `ERREUR` | Plantage technique, capturé et tracé, jamais propagé |

Séparer `NON_APPLICABLE` de `ERREUR` est ce qui rend lisible la *control
coverage view* demandée au brief : savoir que 9 règles sur 15 ne concernent pas
ce fichier n'a rien à voir avec 9 plantages.

Les référentiels ne sont pas des fichiers à fournir : une règle de rapprochement
porte son propre chemin de source (`ref_fichier`), que le moteur charge et
empreinte comme toute autre entrée.

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

### Le catalogue livré ne nomme aucune colonne

L'annexe A.4 exige des contrôles *« independent of datasets (reusable) »*. Une
règle qui nomme `turnover_notionnel` ne l'est pas, quelle que soit sa portée.
Les huit règles livrées ciblent donc des **conventions de nommage**, jamais une
colonne :

| Règle | Dimension | Cible |
|---|---|---|
| Complétude des identifiants | Complétude | `(^\|_)id$` |
| Unicité des identifiants techniques | Unicité | `^(row_id\|record_id\|uuid\|guid)$` |
| Codes devise ISO 4217 | Validité | colonnes disant « code devise » |
| Codes pays ISO 3166-1 | Validité | `(pays\|country)(_code)?$` |
| Montants et quantités ≥ 0 | Validité | `montant\|amount\|prix\|price\|cost\|quantit…` |
| Pourcentages entre 0 et 100 | Validité | `_pct$\|_percent\|taux` |
| Adresses e-mail valides | Validité | `e_mail\|courriel` |
| Fraîcheur de mise à jour | Fraîcheur | `last_update\|updated_at\|_maj$` |

Chaque motif a été confronté aux colonnes réelles de cinq fichiers avant d'être
retenu. Un premier motif « devise » plus large a été **écarté** : il signalait
22 063 lignes sur 57 918 du fichier BIS — les totaux `TO1`. Une règle qui échoue
sur quatre lignes sur dix d'un fichier légitime n'est pas un contrôle, c'est du
bruit.

Cohérence et Réconciliation ne figurent pas au socle : elles exigent de nommer
deux colonnes précises ou une seconde source. La cohérence est couverte par les
suggestions, fichier par fichier ; la réconciliation par une règle que
l'utilisateur crée en désignant le fichier de référence.

### Le fichier propose ses propres contrôles

Un catalogue est muet devant un fichier qu'il ne connaît pas. Sans réponse à ce
problème, la couche n'est pas *plug-and-play* : elle est plug-and-**wait**.

`engine/suggestions.py` déduit donc des contrôles candidats du seul profil, ce
que le brief autorise explicitement (§14, « Frugal by design ») :

> *« Statistical methods : profiling, outlier detection […] useful where no
> fixed threshold can be agreed in advance. »*
> *« Suggesting candidate rules from a dataset profile […] assists the analyst,
> never issues the control verdict. »*

Aucun nom de colonne, aucun domaine métier, aucun seuil n'est écrit dans ce
module. Tout sort de la distribution observée :

| Dimension | Ce qui est observé | Contrôle proposé |
|---|---|---|
| Complétude | colonne pleine, ou trouée à moins de 5 % | `NOT_NULL` |
| Unicité | plus de 95 % de valeurs distinctes | `UNIQUE_KEY` |
| Validité | valeurs négatives isolées, ou au-delà de Q3 + 3·IQR | `RANGE` |
| Validité | faible cardinalité, modalités marginales | `IN_DOMAIN` |
| Validité | forme dominante des valeurs (`P00026` → `^[A-Za-z]\d{5}$`) | `MATCHES_REGEX` |
| Fraîcheur | ancienneté de la donnée la plus récente | `FRESHNESS` |
| Cohérence | ordre respecté entre deux colonnes de date | `DATE_ORDER` |

La réconciliation est la seule dimension non proposable : elle suppose une
seconde source, que seul un humain peut désigner.

Chaque proposition est **chiffrée** — combien de lignes elle signalerait
aujourd'hui — en réutilisant les exécuteurs du moteur, jamais une logique
parallèle. L'analyste coche, le catalogue s'enrichit, le journal trace le motif.
Le module n'écrit jamais rien de lui-même.

Deux garde-fous évitent le bruit :

- une proposition qui signalerait plus de **5 % des lignes** est écartée : à ce
  volume, ce n'est plus un défaut isolé mais la forme de la distribution. C'est
  ce qui empêche de proposer une borne haute sur des montants financiers, dont
  la traîne est naturelle ;
- sous **20 lignes**, aucune statistique n'est tirée.

### Ce qui rend une règle universelle

Une règle cible soit une **colonne** nommée (`{"column": "montant"}`), soit un
**motif de nom de colonne** (`{"colonnes_motif": "(?i)(^|_)id$"}`). Combiné à une
portée `*`, le motif fait qu'une seule ligne de catalogue s'applique à tout
fichier — y compris à ceux qui n'existent pas encore, sans rien déclarer.

DQ02 en est la démonstration : écrite pour les identifiants du fichier BIS, elle
contrôle sans modification les colonnes `patient_id`, `encounter_id` et
`hospital_id` d'un fichier hospitalier déposé pour la première fois.

La **portée** (`dataset_scope`) se lit sur le nom du fichier, sans extension :
`*` pour tous, `ventes_*` pour une famille, `bis_turnover, inventaire` pour une
liste. Là encore, aucun enregistrement préalable n'est nécessaire.

Une exception assumée : **l'unicité d'une clé métier ne se devine pas.** Le
profilage repère les colonnes sans doublon, mais une clé dupliquée n'apparaîtrait
justement pas comme unique — le raisonnement serait circulaire. Une règle
d'unicité nomme donc explicitement ses colonnes.

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
| `COUVERTURE` | Dimensions × fichier, règles hors périmètre et non applicables, répartition par sévérité |

### Le tableau de bord

L'écran ③ Restitution porte trois graphiques, choisis par la fonction que
remplit la donnée — la forme d'abord, la couleur en dernier :

- **Statut des contrôles par dimension** — part-à-tout par catégorie, donc
  barres empilées horizontales (les noms de dimensions sont longs).
- **Lignes en écart par règle** — magnitude, donc barres horizontales, une seule
  teinte, étiquetées directement.
- **Historique du fichier** — évolution, donc une courbe à série unique, lue
  dans les packs de preuves : aucune base n'est tenue à côté, c'est la couche
  d'audit qui porte l'historique.

L'ordre d'empilement des statuts n'est pas cosmétique. Vert et rouge côte à côte
mesurent un écart **CVD de 4,1** en deutéranopie : deux segments indistinguables
pour un daltonien. Intercaler le gris (hors périmètre) et le jaune (erreur
technique) porte le pire écart adjacent à **10,7**, au-dessus du seuil de 8.
Chaque couleur de statut est en outre doublée d'une pastille et d'un libellé :
la couleur ne porte jamais seule le sens. La teinte des barres de magnitude
(`#2a78d6`) tient 4,30:1 sur fond clair et 3,94:1 sur fond sombre, donc sans
connaître le thème de l'utilisateur.
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
   `bis_turnover_demo.csv`. Le système affiche la structure qu'il a déduite,
   liste les règles dont la portée couvre le fichier, puis lance. **Le nombre de contrôles en échec s'affiche en
   premier, en grand**, suivi de qui doit faire quoi.
2. **Le classeur** (90 s) — télécharger le rapport. Même hiérarchie : les
   échecs d'abord, chaque règle traduite en français, puis exceptions,
   couverture, preuves. C'est ce que le métier reçoit.
3. **Créer une règle sans être data** (2 min) — écran *Règles de contrôle* →
   *Créer une règle*. On choisit « Ne jamais être vide », pas `NOT_NULL`. Le
   récapitulatif écrit la phrase en français. Le bouton reste bloqué tant que
   l'action de remédiation manque : on ne peut pas produire une règle orpheline.
4. **Le validateur** (1 min) — créer une règle sans fichier de référence, ou
   avec une expression régulière invalide : refus immédiat, message clair, avant
   toute exécution. Une règle malformée ne casse jamais un contrôle. Une règle
   bien formée mais visant une colonne absente n'est pas rejetée pour autant :
   elle sera simplement déclarée hors périmètre, avec son motif.
5. **La suppression refusée** (30 s) — cliquer Supprimer. L'outil explique que
   les rapports déjà produits doivent rester explicables, et oriente vers
   *Mettre en pause*.
6. **Le fichier d'un autre métier** (2 min) — déposer un fichier hospitalier
   que personne n'a décrit. La structure s'affiche, déduite. L'onglet
   *Contrôles proposés par le fichier* en propose **28, couvrant 5 dimensions**,
   dont 12 signalent déjà un écart : 12 identifiants patients vides, 5 doublons
   de séjour, 10 sorties antérieures à l'admission, 6 coûts négatifs, des âges
   à 999 et des modalités `X`/`UNKNOWN`. On coche, on ajoute au catalogue, on
   lance. **Zéro déclaration, zéro règle écrite, zéro ligne de code.**
7. **La reproductibilité** (30 s) — relancer le même fichier. Empreintes
   identiques, résultats identiques, `run_id` différent.

---

## Arborescence

```
catalogue/store.json        source de vérité (templates, contrôles, journal)
engine/store.py             CRUD + versionnement + journal d'audit
engine/dq_engine.py         validateur, 12 exécuteurs, runner, evidence pack
engine/profiler.py          déduction de structure — remplace toute déclaration
engine/suggestions.py       contrôles candidats déduits du profil (§14 du brief)
engine/prepare_bis.py       préparation des données et des référentiels
engine/bootstrap_store.py   initialisation du catalogue
engine/apply_calibration.py calibrages tracés du premier run
engine/add_libelles_metier.py couche de langage métier des templates
engine/fusion_contrats.py   fusion des contrats redondants (historique)
data/entrees/               fichiers déposés via l'interface
reporting/excel_report.py   classeur six onglets
ui/app.py                   interface Streamlit, quatre écrans du cycle de vie
tests/test_dq.py            85 tests, sans dépendance externe
data/raw/                   source BIS d'origine
data/prepared/              format long + extrait de démonstration
data/ref/                   référentiels devises et pays
evidence/<run_id>/          preuves d'exécution
reporting/sorties/          classeurs générés
```
