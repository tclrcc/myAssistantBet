# Pre-enregistrement — le coin SAFE x confiance 4

**Ce fichier n'est pas revisable.** Il est verse au depot le 11/08/2026, avant
toute collecte des donnees qui l'eprouveront. Toute modification de son contenu
apres cette date invalide ce qu'il pretend garantir ; s'il faut poser une autre
hypothese, elle se pose dans un **autre** fichier, et celui-ci reste tel quel
avec son resultat inscrit.

Sa raison d'etre : tout ce que ce projet a mesure jusqu'ici tient sur un
contraste de quarante points **decouvert apres avoir choisi une partition**. La
partition etait justifiee a priori pour des raisons de procede — c'est ce qui la
sauve — mais son effet a ete constate ensuite, et a chaque tour une raison
legitime de re-decouper s'est presentee. C'est la mecanique par laquelle un
resultat finit toujours par apparaitre.

---

## 1. Ce qui a ete observe, et comment il a ete trouve

Population : les **67** selections tranchees dont l'anteriorite est etablie
(`picks.created_at < events.commence_time`), sur les 104 en base au 11/08/2026.

|                | confiance 3   | confiance 4   |
| -------------- | ------------- | ------------- |
| 🟢 SAFE        | 4/12 — 33 %   | **18/22 — 82 %** |
| 🔵 FUN         | 8/25 — 32 %   | 0/4 — 0 %     |
| 🟠 ULTRA FUN   | 0/4 — 0 %     | —             |

**Le signal n'est pas un axe, c'est une cellule.** Le coin `SAFE ∩ confiance 4`
fait 18/22 quand tout le reste fait 12/45 — Fisher bilateral p = 0,000024.

Ni l'un ni l'autre des deux axes ne survit au conditionnement sur l'autre :

- le palier, a confiance fixee : Mantel-Haenszel exact **p = 0,115** ;
- la confiance, a palier fixe : Mantel-Haenszel exact **p = 0,119**.

**La cellule a ete trouvee en regardant le tableau.** Elle n'etait pas prevue,
et c'est precisement ce que la replication doit eprouver. Ce fichier ne
pretend pas qu'elle est vraie ; il fixe d'avance ce qui compterait comme
confirmation, pour que la reponse ne puisse plus etre choisie apres coup.

### Ce qui a deja ete elimine

- **Une bonne soiree.** Le contraste survit a chaque retrait de strate : sans la
  session la plus lourde (41 % de la cellule) p = 0,0031 ; cette session seule
  p = 0,0040 ; hors marches de vainqueur p = 0,018 ; tennis seul p = 0,0099 ;
  football seul p = 0,0017.
- **Un artefact de nettoyage.** L'echelle ne s'affaiblit pas sur les 37
  selections ecartees, elle **s'inverse** — conf 4 y fait 43 % contre 59 % pour
  conf 3. L'interaction entre strates est confirmee sur la confiance (Zelen
  exact p = 0,0116) et non concluante sur le palier (p = 0,0645).

### Ce qui n'a pas ete elimine, et ne peut pas l'etre retrospectivement

Le bloc de retour d'experience a **effectivement** transmis des taux, sur
9 prompts de 3 sessions (06, 07 et 08/08), quand les seuils valaient encore 10
et 4. Le prompt 58 du 08/08 annoncait mot pour mot « confiance 4 — 10/15, 67 % »
et « confiance 3 — 11/28, 39 % » **avant** que soient produites les etiquettes
dont on mesure aujourd'hui qu'elles valent 69 % et 29 %.

Ces 3 sessions fournissent **38 des 67**. Le contraste vaut p = 0,0045 dans les
sessions alimentees et p = 0,140 dans les vierges ; les deux regimes ne
different pas (Zelen exact p = 0,587), mais les 29 selections vierges ne
suffisent pas a l'etablir seules.

**Ni etabli, ni exclu.** C'est pourquoi la fenetre de replication se collecte le
bloc coupe (§4).

---

## 2. Le test — un seul

| Champ | Valeur |
| --- | --- |
| Hypothese | Les selections `SAFE ∩ confiance 4` reussissent plus souvent que les autres |
| Population | Anteriorite etablie : `picks.created_at < events.commence_time` |
| Donnees | **Fraiches uniquement** — selections creees strictement apres le 11/08/2026 |
| Test | Fisher exact **unilateral**, cellule contre le reste |
| Seuil | α = 0,05 |
| Cible | **21 selections dans le bras cellule** (`required_sample` sur l'ecart observe) |
| Horizon | ~7 sessions au debit constate — voir §3 |
| Regle d'arret | Au **compte dans la cellule**, jamais en sessions |

Unilateral parce que la direction est fixee **ici**, avant les donnees. C'est le
seul test de ce fichier : il n'y a pas d'hypothese secondaire, pas de sous-groupe
prevu, pas de variante.

**Le contraste de confiance n'est pas pre-enregistre**, et ce n'est pas un
oubli : il est deja refute conditionnellement (Mantel-Haenszel p = 0,119).
L'inscrire reviendrait a acheter un billet dont on sait qu'il est perdant, en
esperant qu'il sorte.

---

## 3. L'horizon se compte dans la cellule

La cellule porte 22 des 67 selections, soit **33 % du volume**. A 9,6 selections
tranchees a anteriorite etablie par session, elle se remplit a **~3,2 par
session** : 21 dans le bras cellule demandent donc **~7 sessions**, et non 5.

**La regle d'arret se compte en selections dans la cellule, jamais en sessions.**
Couper le bloc de retour d'experience peut deplacer la distribution
d'etiquetage — c'est meme un effet possible du remede — et un compteur en
sessions clorait alors la fenetre sur un echantillon vide.

---

## 4. Predictions descriptives — enoncees, non testees

Le tableau ne decrit pas un **gradient** mais un **seuil**. Les deux cellules a
confiance 3 sont a un point l'une de l'autre (33 % et 32 %) : l'echelle ne gradue
pas, elle ouvre ou ferme une porte.

Deux predictions chiffrees d'avance, qui distinguent les deux lectures :

1. les cellules hors coin resteront **entre 20 et 40 %** ;
2. leur ecart entre elles restera **sous 15 points**.

Elles ne sont pas testees et ne coutent donc **aucune multiplicite**. Si elles
echouent pendant que le test du §2 passe, le resultat est vrai et l'explication
est fausse — et ce fichier aura servi a le savoir.

---

## 5. Le regime de collecte

Le bloc de taux du prompt est **suspendu** pendant toute la fenetre. Il est
aujourd'hui tu par ses seuils (40 selections tranchees, 10 journees), mais un
reglage abaisse le rouvrirait sans que personne s'en apercoive : la suspension
est donc explicite et non deleguee a un seuil.

Le prompt dit qu'une replication est en cours, plutot que « recul insuffisant » —
une phrase qui deviendra fausse pendant la fenetre, et une fausse explication a
l'endroit meme ou l'on cherche a etre rigoureux serait le pire endroit ou en
mettre une.

---

## 6. Regle d'echec

Si le contraste ne se reproduit pas sur les donnees fraiches, **l'echelle est
declaree non validee** et ce pre-enregistrement n'est pas reecrit.

**Et si la cellule echoue tandis qu'une autre cellule reussit sur ces memes
donnees fraiches, ce n'est pas un resultat** : c'est le meme chemin parcouru une
seconde fois. Une telle cellule ne peut etre pre-enregistree que dans un fichier
distinct, contre une **troisieme** collecte, et le present echec reste inscrit.

Sans cette clause, un echec produirait mecaniquement le tour suivant — ce qui est
exactement le defaut que ce fichier existe pour arreter.

---

## 7. Resultat

*A completer une seule fois, quand la cible du §2 est atteinte.*

| Champ | Valeur |
| --- | --- |
| Date de cloture | — |
| Selections fraiches dans la cellule | — |
| Selections fraiches hors cellule | — |
| p (Fisher unilateral) | — |
| Verdict | — |
| Predictions descriptives §4 | — |
