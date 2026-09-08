# Protocole de test — DQ Compass

À dérouler avant la présentation. Chaque étape dit **ce que vous faites** et
**ce que vous devez voir**. Si l'écran ne montre pas ce qui est annoncé,
c'est un bug : notez-le, ne l'improvisez pas devant le jury.

Comptez 25 minutes pour tout dérouler.

---

## 0. Préparer (2 min)

Ouvrez un terminal dans le dossier du projet.

```bash
cd "c:/Users/yassi/Desktop/DQ Project"
./.venv/Scripts/python.exe tests/test_dq.py
```

> **Attendu :** la dernière ligne affiche `60/60 tests reussis`.
> Si un test échoue, réglez-le avant de continuer : l'interface s'appuie
> dessus.

Puis lancez l'interface :

```bash
./.venv/Scripts/python.exe -m streamlit run ui/app.py
```

> **Attendu :** le navigateur s'ouvre sur `http://localhost:8501` (ou 8555 si
> l'instance précédente tourne encore). Barre latérale « 🧭 DQ Compass », avec
> *Règles en service* et *Fichiers connus*.

⚠️ Toujours `./.venv/Scripts/python.exe`, jamais `python` seul : le Python
global de la machine a un pandas cassé.

---

## 1. Contrôler un fichier reconnu (4 min)

**Vous faites :** écran *Contrôler un fichier* → onglet *Choisir un fichier
déjà présent* → `bis_turnover_demo.csv`.

> **Attendu, avant même de lancer :**
> - bandeau vert « **Fichier reconnu : BIS Triennial Survey — OTC derivatives
>   turnover** », *100 % des colonnes attendues sont présentes*, *57 918 lignes,
>   26 colonnes* ;
> - la liste des **15 règles** qui s'appliquent ;
> - un aperçu dépliable des 50 premières lignes.

**Vous faites :** *Lancer le contrôle*.

> **Attendu :**
> - le gros bandeau annonce **« 1 contrôle en échec »**, en rouge, *dont 1 de
>   gravité Bloquant ou Important* ;
> - juste en dessous : **DQ09 · Intégrité référentielle — devise jambe 2**,
>   *32 lignes en écart sur 57 918*, responsable *Referential Management*, et
>   l'action à mener ;
> - les compteurs secondaires : 15 contrôles, 14 sans écart, 46 lignes en
>   exception, 0 règle refusée.

**Le point à vérifier :** le nombre d'échecs est **la première chose lisible**.
Le taux de conformité n'apparaît qu'ensuite, dans le classeur.

**Vous faites :** onglet *Détail des contrôles*.

> **Attendu :** une colonne **« Ce qui est vérifié »** en français, du type
> *« Chaque DER_CURR_LEG2 doit exister dans le référentiel ref_devises »*.
> Aucun `template_id`, aucun JSON.

---

## 2. Le classeur Excel (3 min)

**Vous faites :** *Télécharger le rapport Excel*, puis ouvrez-le.

> **Attendu, onglet `SYNTHESE` :**
> - première tuile, en très gros et sur fond rouge : **1**, libellé
>   `CONTROLES EN ECHEC` ;
> - deuxième : `dont Bloquant / Important` ;
> - le taux de conformité en **quatrième** position seulement ;
> - le tableau trié **échecs en tête**, avec la colonne
>   `ce_qui_est_verifie` en français.

> **Attendu, les six onglets :** `SYNTHESE`, `EXCEPTIONS`, `COUVERTURE`,
> `EVIDENCE`, `CATALOGUE_EXECUTE`, `JOURNAL`.

**À montrer au jury :** l'onglet `EVIDENCE` porte les empreintes SHA-256 du
fichier contrôlé **et** du catalogue. C'est ce qui rend le run rejouable.

---

## 3. Créer une règle sans être data (5 min)

C'est l'étape qui répond au « n'importe qui doit pouvoir s'en servir ».

**Vous faites :** *Règles de contrôle* → *Créer une règle*.

> **Attendu, étape 1 :** une liste de choix en français — *Ne jamais être
> vide*, *Ne pas contenir de doublon*, *Faire partie d'une liste autorisée*,
> *Rester dans des bornes chiffrées*… Chaque choix affiche son explication et
> un exemple concret. **Aucun `NOT_NULL` visible.**

**Vous faites :** choisissez *Rester dans des bornes chiffrées*, fichier
`bis_turnover`, colonne `turnover_notionnel`, cochez `min` = 0.

> **Attendu, encadré Récapitulatif :**
> *« La colonne « turnover_notionnel » doit être supérieure ou égale à 0. »*
> La phrase se réécrit à chaque changement.

**Vous faites :** essayez d'enregistrer **sans** remplir le nom ni l'action de
remédiation.

> **Attendu :** message *« Il manque encore le nom de la règle, la raison
> d'être de la règle, l'action à mener en cas d'écart »*, et le bouton
> **Enregistrer reste grisé**.

C'est ce garde-fou qui manquait : le contrôle `DQ17` du catalogue a été créé
sans description ni remédiation, avec un seuil à 0,9 % saisi par mégarde. Ce
cas n'est plus possible.

**Vous faites :** complétez les trois champs, enregistrez.

> **Attendu :** message vert *« Règle DQ18 créée »*, et la règle apparaît dans
> la liste avec sa phrase en français.

---

## 4. Le validateur refuse une règle absurde (2 min)

**Vous faites :** *Créer une règle* → *Rester dans des bornes chiffrées* →
colonne **`DER_BASIS`** (une colonne de texte).

> **Attendu :** encadré rouge *« La règle est refusée par le validateur :
> RANGE exige une colonne numerique ; 'DER_BASIS' est declaree 'string' »*,
> bouton Enregistrer grisé.

**Le point :** le refus arrive **avant** toute exécution. Une règle mal
définie ne peut pas casser un contrôle en cours.

---

## 5. La suppression est refusée (1 min)

**Vous faites :** *Règles de contrôle* → sélectionnez `DQ03` dans *Agir sur
une règle existante* → *🗑️ Supprimer*.

> **Attendu :** message d'erreur expliquant que les rapports déjà produits
> doivent rester explicables, et orientant vers *Mettre en pause*.

**Vous faites :** *⏸️ Mettre en pause*, puis allez dans *Journal*.

> **Attendu :** en haut du journal, une ligne horodatée à votre nom, action
> `MODIFICATION`, champ `statut`, `Actif` → `Suspendu`, avec votre motif.
> Une seconde ligne `VERSION` montre la version passée de 2 à 3.

**Pensez à remettre `DQ03` en service** avant la démo (*▶️ Remettre en
service*).

---

## 6. Le fichier inconnu — la séquence forte (6 min)

C'est ce qui prouve la généricité. Un fichier de test est déjà prêt :
`data/exemples/inventaire.csv`, sans rapport avec les données BIS.

**Vous faites :** *Contrôler un fichier* → onglet *Déposer un fichier* →
déposez `data/exemples/inventaire.csv`.

> **Attendu :** bandeau orange *« Fichier non reconnu. Le plus proche est
> Référentiel des devises avec 33 % des colonnes attendues, sous le seuil de
> 70 % »*, avec un renvoi vers l'écran *Fichiers connus*.

**Vous faites :** *Fichiers connus* → onglet *➕ Décrire un nouveau fichier*.
Renseignez :

| Champ | Valeur |
|---|---|
| Nom technique | `inventaire` |
| Nom métier | `Inventaire des stocks` |
| Chemin | `data/exemples/inventaire.csv` |
| Responsable | `Supply Chain` |

Et collez ces six lignes dans **Colonnes** :

```
article_id;string;identifiant;OUI;OUI;;
libelle;string;libelle;NON;OUI;;
code_devise;string;code;NON;OUI;ref_devises;code_devise
quantite;integer;mesure;NON;OUI;;
prix_unitaire;decimal;mesure;NON;OUI;;
date_inventaire;date;date_evenement;NON;OUI;;
```

> **Attendu :** *« Fichier « Inventaire des stocks » enregistré avec 6
> colonnes. 2 règle(s) s'y appliquent déjà. »*

**C'est la phrase qui compte.** Vous n'avez écrit aucune règle : `DQ02`
(complétude des identifiants) et `DQ07` (unicité des clés primaires) ciblent
un **rôle**, pas une colonne, et se propagent seules à tout fichier déclaré.

**Vous faites :** retour à *Contrôler un fichier*, redéposez
`inventaire.csv`, lancez.

> **Attendu :**
> - *« Fichier reconnu : Inventaire des stocks — 100 % »* ;
> - **2 contrôles en échec** ;
> - `DQ02` : 1 ligne en écart sur 6 — l'identifiant vide ;
> - `DQ07` : 2 lignes en écart sur 6 — `ART-002` en double ;
> - 3 lignes en exception au total.

**La phrase à dire au jury :** *« Zéro règle écrite, zéro ligne de code
modifiée, un fichier entièrement contrôlé. »*

---

## 7. La reproductibilité (2 min)

**Vous faites :** relancez exactement le même fichier.

> **Attendu :** mêmes résultats, mêmes empreintes SHA-256 dans l'onglet
> `EVIDENCE`, mais un `run_id` différent.

Chaque exécution a laissé un dossier dans `evidence/`. Ouvrez le dernier :

```bash
ls evidence/ | tail -1
```

> **Attendu :** six fichiers — `manifest.json`, `catalogue_snapshot.json`,
> `results.json`, `rejets.json`, `exceptions.csv`, `execution.log`.

`catalogue_snapshot.json` est une **copie figée** des règles telles
qu'exécutées, pas un lien vers le catalogue vivant : c'est ce qui permet
d'expliquer un rapport d'il y a trois mois même si les règles ont changé
depuis.

---

## Avant la présentation

- [ ] `60/60 tests reussis`
- [ ] `DQ03` remis en service après le test de mise en pause
- [ ] Le contrat `inventaire` **supprimé du catalogue** si vous voulez rejouer
      l'étape 6 en direct (sinon le fichier sera déjà reconnu)
- [ ] Une capture d'écran de chaque étape, en secours si la démo live plante
- [ ] Le fichier `data/entrees/DQ_Rapport_*.xlsx` déposé par erreur pendant les
      essais : à supprimer, il encombre la liste de sélection

Pour remettre le catalogue dans son état de départ :

```bash
git checkout catalogue/store.json
```

> ⚠️ Cette commande annule **toutes** vos modifications de règles, y compris
> celles que vous voudriez garder. Vérifiez d'abord avec `git diff --stat`.
