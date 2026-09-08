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

> **Attendu :** la dernière ligne affiche `90/90 tests reussis`.
> Si un test échoue, réglez-le avant de continuer : l'interface s'appuie
> dessus.

Puis lancez l'interface :

```bash
./.venv/Scripts/python.exe -m streamlit run ui/app.py
```

> **Attendu :** le navigateur s'ouvre sur `http://localhost:8501` (ou 8555 si
> l'instance précédente tourne encore). Barre latérale « 🧭 DQ Compass », avec
> *Règles en service*, *Dimensions couvertes* et *Règles universelles*, puis
> quatre écrans nommés d'après le brief : *① Catalogue de contrôles*
> (définit), *② Exécution* (exécute), *③ Restitution* (restitue),
> *④ Piste d'audit* (prouve). Aucun écran ne demande de décrire quoi que ce
> soit.

⚠️ Toujours `./.venv/Scripts/python.exe`, jamais `python` seul : le Python
global de la machine a un pandas cassé.

---

## 1. Contrôler un fichier — écran ② Exécution (4 min)

**Vous faites :** écran *② Exécution* → onglet *Choisir un fichier
déjà présent* → `bis_turnover_demo.csv`.

> **Attendu, avant même de lancer :**
> - bandeau vert *« bis_turnover_demo.csv — 57 918 lignes, 26 colonnes.
>   Structure déduite à la lecture, aucune déclaration nécessaire »* ;
> - la **structure déduite**, dépliée : une ligne par colonne, avec le type
>   déduit du contenu (`annee` en *integer*, `date_observation` en *date*,
>   `turnover_notionnel` en *decimal*), le taux de vide et les colonnes sans
>   doublon. **Personne n'a saisi cela** ;
> - l'identifiant de ligne retenu pour les exceptions : `observation_id` ;
> - la liste des **15 règles** dont la portée couvre `bis_turnover_demo` ;
> - un aperçu dépliable des 50 premières lignes.

**Vous faites :** *Lancer le contrôle*.

> **Attendu :**
> - le gros bandeau annonce **« 1 contrôle en échec »**, en rouge, *dont 1 de
>   gravité Bloquant ou Important* ;
> - juste en dessous : **DQ09 · Intégrité référentielle — devise jambe 2**,
>   *32 lignes en écart sur 57 918*, responsable *Referential Management*, et
>   l'action à mener ;
> - les compteurs secondaires : 15 contrôles, 14 sans écart, 0 hors périmètre,
>   46 lignes en exception, 0 règle refusée.

**Le point à vérifier :** le nombre d'échecs est **la première chose lisible**.
Le taux de conformité n'apparaît qu'ensuite, dans le classeur.

**Vous faites :** écran *③ Restitution*.

> **Attendu :** un bandeau d'échecs, six tuiles, puis deux graphiques —
> statut par dimension, et lignes en écart par règle. Dans l'onglet *Détail des
> contrôles*, une colonne **« Ce qui est vérifié »** en français, du type
> *« Chaque DER_CURR_LEG2 doit exister dans le fichier de référence
> data/ref/ref_devises.csv »*. Aucun `template_id`, aucun JSON.

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

**Vous faites :** *① Catalogue de contrôles* → *Créer une règle*.

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

**Vous faites :** *① Catalogue de contrôles* → sélectionnez `DQ03` dans *Agir sur
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

## 6. Le fichier d'un autre métier — la séquence forte (4 min)

C'est ce qui prouve la généricité. Prenez **n'importe quel CSV** : un export de
votre poste, un fichier du jury, `data/exemples/inventaire.csv`. La séquence
ci-dessous est décrite avec un export hospitalier de 1 000 séjours, mais rien
dans le code ne connaît ce fichier.

**Vous faites :** *② Exécution* → *Déposer un fichier* → votre fichier.

> **Attendu, immédiatement :**
> - quatre tuiles : lignes, colonnes, règles dans le périmètre, contrôles
>   proposés ;
> - la **structure déduite** : types, taux de vide, colonnes sans doublon —
>   personne ne les a saisis ;
> - un avertissement franc si aucune règle du catalogue ne cible ce fichier.
>   C'est le cas normal d'un fichier jamais rencontré.

**Vous faites :** onglet *Contrôles proposés par le fichier*.

> **Attendu :** des propositions groupées par dimension, chacune portant ce
> qu'elle **signalerait aujourd'hui**. Sur le fichier hospitalier : 28
> propositions couvrant 5 dimensions, dont 12 avec un écart constaté —
> 12 identifiants patients vides, 5 doublons de séjour, 10 sorties antérieures
> à l'admission, 6 coûts négatifs, des âges hors bornes, des modalités `X` et
> `UNKNOWN`. Celles qui trouvent quelque chose sont pré-cochées.

**Le point à comprendre :** aucune de ces règles n'est écrite dans le code. Elles
sortent de la distribution observée — colonnes toujours remplies, valeurs
marginales, bornes aberrantes, ordre des dates. Le brief autorise explicitement
cette assistance (§14) ; le verdict, lui, reste au moteur.

**Vous faites :** cochez, laissez la portée sur *ce fichier*, *Ajouter au
catalogue*, puis *Lancer le contrôle*.

> **Attendu :** les règles apparaissent au catalogue avec leurs 14 attributs,
> le journal porte le motif *« Déduite du profil de … »*, et le contrôle
> remonte exactement les écarts annoncés.

**Le contre-test qui compte :** déposez un fichier dégénéré — une seule ligne,
une colonne entièrement vide, des types mélangés, zéro ligne. Rien ne doit
tomber : ni profil, ni suggestion, ni exécution. C'est vérifié par le test
*robustesse : la chaîne complète tient sur des fichiers dégénérés*.

**La phrase à dire au jury :** *« De la finance à la santé : zéro déclaration,
zéro règle écrite, zéro ligne de code modifiée. »*

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

- [ ] `90/90 tests reussis`
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
