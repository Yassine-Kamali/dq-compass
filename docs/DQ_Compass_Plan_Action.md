# DQ Compass — Plan d'action de l'équipe

Datathon MBA-ESG / SG GSC — 7 au 9 septembre 2026

---

## 0. Le principe directeur

Une seule phrase à garder en tête pendant trois jours :

> **Le jury évalue le cycle complet Définition → Exécution → Reporting → Audit, pas la sophistication technique.**

Trois conséquences pratiques :

1. **Zéro IA obligatoire.** Python + pandas + un fichier de configuration suffisent. La slide 14 le dit : un contrôle déterministe qui tourne, s'explique et se rejoue est mieux noté qu'un modèle non reproductible.
2. **Aucun nom de colonne, aucun seuil, aucun dataset codé en dur dans le moteur.** Tout vient du catalogue.
3. **Mieux vaut un périmètre restreint qui tourne de bout en bout qu'un périmètre large à moitié fait.** Les six dimensions sont le périmètre minimum, pas un objectif ambitieux.

---

## 1. Architecture cible

Quatre composants, décidés une fois pour toutes lundi après-midi.

| # | Composant | Choix technique | Rôle |
|---|---|---|---|
| 1 | Control Catalogue | Fichier Excel **ou** YAML éditable | DÉFINIT les contrôles (14 attributs obligatoires) |
| 2 | DQ Engine | Python + pandas, 6–8 exécuteurs génériques | EXÉCUTE les contrôles, quel que soit le dataset |
| 3 | Reporting Layer | HTML statique généré par le moteur (ou Streamlit) | RESTITUE scorecard, exceptions, couverture |
| 4 | Audit Layer | Dossier `/evidence/<run_id>/` généré automatiquement | PROUVE l'exécution, rejouable |

### Le catalogue à deux niveaux (point le plus important)

- **Modèle de règle (template)** — générique, indépendant de tout dataset : `NOT_NULL`, `MATCHES_REGEX`, `UNIQUE_KEY`, `CROSS_FIELD_EQUALS`, `FRESHNESS_MAX_LAG`, `SUM_RECONCILIATION`. Implémentés une seule fois dans le moteur.
- **Instance de contrôle** — un modèle relié à un dataset, un champ, un seuil, un propriétaire, une sévérité. C'est une **ligne du catalogue**.

Ajouter un contrôle = ajouter une ligne. Jamais une ligne de code.

### Gouvernance du catalogue

Pas de suppression définitive : l'audit exige la reconstruction des runs historiques. Chaque contrôle porte :

- `statut` : Actif / Suspendu / Déprécié
- `version` : incrémentée à chaque modification, jamais écrasée
- `effective_from` : date d'entrée en vigueur

---

## 2. Étapes détaillées

### E0 — Questions à SG GSC (lundi 16h00, visioconférence)

À poser dans cet ordre, ce sont des questions bloquantes :

1. **Des jeux de données sont-ils fournis, ou générons-nous nos propres données synthétiques ?** Le brief n'en mentionne aucun.
2. Quel EUC cible avez-vous en tête pour l'intégration : Excel, Alteryx, autre ?
3. Attendez-vous une démo live du prototype ou une présentation de la conception ?
4. Le catalogue doit-il être livré dans un format particulier ?

**Fini quand :** les réponses sont écrites et partagées dans le groupe.

---

### E1 — Figer le schéma du catalogue (lundi, 1h)

Créer le fichier avec les 14 attributs imposés par la slide 18, plus les 3 champs de gouvernance.

| Colonne | Exemple |
|---|---|
| rule_id | DQ01 |
| control_name | Complétude identifiant client |
| control_type | Completeness |
| description | L'identifiant client doit être renseigné sur toute transaction |
| rule_template | NOT_NULL |
| logic_definition | `client_id IS NOT NULL` |
| dataset_scope | transactions |
| data_element | client_id |
| threshold | 0% de nulls |
| severity | High |
| frequency | Par run |
| owner | Data Steward Transactions |
| output_type | Error dataset + taux de complétude |
| kpi | % de complétude |
| remediation_action | Rejet en quarantaine + retour au producteur sous 24h |
| statut | Actif |
| version | 1.0 |
| effective_from | 2026-09-07 |

**Fini quand :** le fichier existe, personne ne le remet en cause, et 3 règles de test sont déjà saisies.

> Ne commencez pas à coder avant que ce schéma soit figé. Si vous inversez l'ordre, vous refactorez mardi soir.

---

### E2 — Préparer les jeux de données (lundi, 2h — en parallèle de E1)

**Deux datasets sans aucun rapport métier entre eux.** Par exemple :

- `transactions.csv` — id, client_id, montant, devise, date_operation, date_valeur
- `fournisseurs.csv` — code_fournisseur, raison_sociale, SIRET, pays, date_maj

Plus un troisième petit fichier pour la réconciliation : `totaux_source.csv` (totaux attendus par devise).

**Injecter volontairement des anomalies**, documentées dans un fichier `anomalies_attendues.md` :

- ~2% de `client_id` vides → doit faire tomber DQ01
- quelques SIRET à 13 chiffres → doit faire tomber la règle de validité
- 5 lignes dupliquées → uniqueness
- des `date_valeur` antérieures à `date_operation` → consistency
- des `date_maj` vieilles de 10 jours → timeliness
- un écart de 0,7% sur un total → reconciliation

Ce fichier d'anomalies attendues est votre **jeu de test** : il prouve que le moteur détecte ce qu'il doit détecter, ni plus ni moins. C'est aussi un excellent argument devant le jury.

**Fini quand :** les fichiers sont générés et les anomalies documentées.

---

### E3 — Le moteur (mardi matin, priorité absolue)

Ordre d'implémentation, à ne pas modifier :

1. **Chargeur de catalogue** — lit le fichier, valide chaque ligne, renvoie une liste d'instances de contrôle.
2. **Validateur de catalogue** — rejette proprement : modèle inconnu, seuil manquant, colonne inexistante dans le dataset, sévérité invalide. Message d'erreur clair, pas de crash.
3. **Les 6 exécuteurs** — chacun ~20 lignes, signature identique :

```
executer(dataframe, parametres) -> {
    statut: PASS | FAIL,
    kpi_valeur: float,
    nb_lignes_testees: int,
    nb_exceptions: int,
    exceptions: dataframe
}
```

4. **L'orchestrateur** — boucle sur les contrôles actifs applicables au dataset, agrège, écrit l'evidence pack.

**Fini quand :** un run complet tourne sur `transactions.csv` avant midi mardi. C'est le jalon critique de tout le datathon.

---

### E4 — L'evidence pack (mardi début d'après-midi, ~2h)

Pour chaque exécution, créer `/evidence/<run_id>/` contenant :

| Fichier | Contenu |
|---|---|
| `run_metadata.json` | run_id (UUID), timestamp UTC, utilisateur, version du moteur |
| `dataset_reference.json` | nom du fichier, **hash SHA-256**, nb de lignes, nb de colonnes |
| `rules_snapshot.json` | copie intégrale des règles utilisées, avec leur version |
| `results.json` | par règle : PASS/FAIL, valeur du KPI, seuil, nb d'exceptions, sévérité |
| `exceptions.csv` | lignes en échec, avec la colonne `rule_id` |
| `execution.log` | horodatage de chaque étape |

Le hash SHA-256 est votre réponse à l'exigence « dataset reference: version, snapshot or hash ». C'est deux lignes de code et ça vaut cher au regard du critère Gouvernance et auditabilité.

**Fini quand :** vous relancez deux fois le même run et vous démontrez que hash et résultats sont identiques.

---

### E5 — Le reporting (mardi après-midi, ~3h)

Trois sorties, dans cet ordre de priorité :

1. **Scorecard feux tricolores** — une ligne par règle : rule_id, dimension, KPI, seuil, statut, sévérité. Vert / Orange / Rouge.
2. **Rapport d'exceptions** — le détail au niveau ligne, filtrable par règle.
3. **Vue de couverture** — quelles dimensions sont couvertes, sur quels datasets, avec quelle sévérité. Elle montre les trous autant que le reste.

Générez du HTML statique depuis le moteur. Streamlit seulement si quelqu'un le maîtrise déjà : ne pariez pas la démo sur un outil découvert la veille.

---

### E6 — Le deuxième dataset (mardi après-midi, ~1h)

**C'est le moment décisif de votre présentation.**

Vous ajoutez des lignes au catalogue pour `fournisseurs.csv`. Vous relancez. Ça marche. **Aucune ligne de code n'a été modifiée.**

La plupart des équipes construiront un beau moteur sur un seul jeu de données. Celles qui basculent d'un dataset à l'autre en direct **prouvent** la généricité au lieu de l'affirmer.

Chronométrez cette démo : elle doit tenir en 90 secondes.

---

### E7 — La documentation (mardi, en parallèle — profils PMIT)

Le brief (§5.3) exige trois choses. Un document, quatre sections :

1. **Architecture et choix de conception** — le schéma des 4 composants, et surtout la justification : pourquoi un catalogue en fichier plutôt qu'une base, pourquoi du déterministe, pourquoi deux niveaux de règles.
2. **Logique des contrôles** — les 6 modèles, leur logique formelle, leurs paramètres.
3. **Limites et scalabilité** — soyez francs, c'est valorisé : volumétrie testée, ce qui casserait à 10 millions de lignes, ce qui manque (ordonnanceur, gestion des droits, base de données, workflow de remédiation).
4. **Mapping superviseur** — reprenez le tableau de l'annexe C du brief et remplissez-le avec **vos** artefacts réels : quelle exigence, quel contrôle, quelle preuve, quelle sortie. Ce tableau seul répond au critère Gouvernance.

---

### E8 — Gel du code (mardi 16h00, non négociable)

Après 16h : plus aucune fonctionnalité. Uniquement correction de bugs, répétition, préparation des supports.

Les équipes qui codent jusqu'à la dernière minute font des démos qui plantent.

---

### E9 — Répétition et plan B (mardi soir)

- Répétition chronométrée complète : **15 min + 5 min de Q&A**.
- **Enregistrer une vidéo de la démo** et faire des captures d'écran de chaque sortie. Si l'ordinateur ou la visio lâche mercredi, vous continuez.
- Préparer les réponses aux trois questions certaines du jury :
  - « Comment ça s'intègre concrètement dans un EUC existant ? »
  - « Que se passe-t-il sur 10 millions de lignes ? »
  - « Qui possède les règles, et qui a le droit de les modifier ? »

---

## 3. Plan par jour

### Lundi 7 septembre

| Heure | Qui | Quoi |
|---|---|---|
| 09h30–11h00 | Tous | Kickoff (amphithéâtre) |
| 11h00–12h00 | Tous | Lecture commune du brief, répartition des rôles, décision d'architecture (**30 min de débat maximum**) |
| 12h00–13h00 | — | Déjeuner |
| 13h00–16h00 | Binômes | E1 schéma du catalogue + E2 génération des datasets, en parallèle. Squelette du repo. |
| 16h00–17h00 | Tous | **Visio SG GSC** — poser les questions E0 |
| 17h00–19h00 | Tech | Premiers exécuteurs (`NOT_NULL`, `UNIQUE_KEY`) sur données réelles |

**Fin de journée :** schéma du catalogue figé, deux datasets prêts avec anomalies documentées, repo initialisé.

---

### Mardi 8 septembre

| Heure | Qui | Quoi |
|---|---|---|
| 09h00–11h00 | Tech | E3 — moteur, validateur, les 6 exécuteurs |
| 11h00–12h00 | Tous | **Visio SG GSC** — valider l'approche, montrer l'avancement |
| 12h00–13h00 | — | Déjeuner |
| 13h00–14h00 | Tech | E4 — evidence pack + hash + test de reproductibilité |
| 14h00–16h00 | Tech / Data | E5 reporting + E6 second dataset |
| 13h00–16h00 | PMIT | E7 — documentation, mapping superviseur, structure du deck |
| **16h00** | — | **GEL DU CODE** |
| 16h00–17h00 | Tous | **Visio SG GSC** — dernières questions |
| 17h00–19h30 | Tous | E9 — répétition chronométrée, vidéo de secours, finalisation du deck |

**Jalon critique :** un run complet doit tourner avant midi. Si ce n'est pas le cas à 12h, réduisez le périmètre — 4 dimensions qui fonctionnent valent mieux que 6 qui plantent.

---

### Mercredi 9 septembre

| Heure | Quoi |
|---|---|
| 09h00 | Arrivée, test du matériel et de la visio, ouverture de tous les fichiers de la démo |
| Avant le passage | Dernière répétition à blanc, vidéo de secours accessible en un clic |
| Passage | 15 min de présentation + 5 min de Q&A |
| Après-midi | Remarques de clôture |

---

## 4. Structure de la présentation (15 minutes)

| Durée | Contenu |
|---|---|
| 1 min | Le problème, en une phrase et un chiffre |
| 2 min | L'architecture : les 4 composants, le cycle de vie complet |
| 2 min | Le catalogue : les 14 attributs, les deux niveaux, la gouvernance par version |
| **4 min** | **Démo live** : run sur dataset 1 → scorecard → drill-down exception → **bascule sur dataset 2 sans changer le code** |
| 2 min | L'evidence pack : ouvrir le dossier, montrer le hash, rejouer le run, résultats identiques |
| 2 min | Le mapping superviseur : exigence → contrôle → preuve → sortie |
| 2 min | Valeur métier, limites assumées, chemin d'industrialisation |

Faites parler un profil PMIT sur la valeur métier et la gouvernance, un profil technique sur la démo. Le brief valorise explicitement la collaboration entre les deux spécialisations.

---

## 5. Répartition des rôles

| Rôle | Effectif | Livrable |
|---|---|---|
| Moteur | 2 | Exécuteurs, orchestrateur, validateur |
| Catalogue & données | 1–2 | Catalogue rempli (12–15 règles), datasets, anomalies attendues |
| Reporting & evidence | 1–2 | Scorecard, exceptions, couverture, evidence pack |
| Documentation & pitch | 1–2 | Doc d'architecture, mapping superviseur, deck, animation de la démo |

Découpez par composant, pas par « les techs codent, les autres regardent ». Le mapping superviseur et le discours de valeur métier sont notés au même titre que le code.

---

## 6. Checklist finale

Avant de présenter, chaque case doit être cochée :

- [ ] Les 6 dimensions sont couvertes par au moins un contrôle qui tourne
- [ ] Les 14 attributs sont présents dans le catalogue
- [ ] Un même moteur tourne sur 2 datasets différents, sans modification de code
- [ ] L'evidence pack contient run_id, timestamp, hash, snapshot des règles, résultats, exceptions, logs
- [ ] Le même run relancé donne exactement le même résultat
- [ ] Le drill-down jusqu'à la ligne en exception fonctionne
- [ ] Une règle mal formée est rejetée proprement, sans crash
- [ ] La désactivation d'une règle fonctionne, sans supprimer sa définition
- [ ] Le champ `remediation_action` est rempli pour chaque contrôle
- [ ] Le mapping superviseur est complété avec vos artefacts réels
- [ ] La vidéo de secours et les captures existent
- [ ] La présentation a été chronométrée sous 15 minutes

---

## 7. Les cinq pièges

1. **Coder avant de figer le catalogue.** Refactoring garanti mardi soir.
2. **Bâcler Timeliness et Reconciliation.** Ce sont celles que les autres équipes oublieront : c'est là que vous vous différenciez.
3. **Oublier `remediation_action`.** Un contrôle qui dit seulement « échec » est moins mature qu'un contrôle qui dit quoi faire et qui doit le faire.
4. **Négliger la couche Audit.** C'est un axe de notation entier et il coûte deux heures.
5. **Démo live sans plan B.** Vidéo + captures, toujours.
