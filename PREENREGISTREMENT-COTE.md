# Pre-enregistrement — le deficit croit-il avec la cote ?

**Verse au depot le 11/08/2026, avant la collecte qui l'eprouvera.** Non
revisable : toute modification apres cette date invalide ce qu'il pretend
garantir. S'il faut poser une autre hypothese, elle se pose dans un **autre**
fichier, et celui-ci reste tel quel avec son resultat inscrit.

C'est le **second** de ce projet. Le premier — un coin du tableau `SAFE ∩
confiance 4` — a ete retire avant d'etre ouvert : ses selections etaient des
favoris courts dont les prix annoncaient deja 69 % quand elles en rendaient
82 %. Le controle par les prix l'a arrete a temps. Ce qui suit est ce que ce
controle a fait apparaitre a la place.

---

## 1. Ce qui a ete observe, et pourquoi la direction est licite

Population : les **73** selections tranchees d'anteriorite etablie
(`picks.created_at < events.commence_time`), au 11/08/2026.

Le deficit au prix croit avec la cote :

| Bande | n | observe | attendu | ecart |
| --- | --- | --- | --- | --- |
| < 1.50 | 21 | 14 | 15,5 | -1,5 |
| 1.50 – 2.00 | 40 | 20 | 23,4 | -3,4 |
| >= 2.00 | 12 | 1 | 5,4 | **-4,4** |

Un tiers du deficit total tient dans douze selections. Sur un decoupage
different — terciles d'effectif — le gradient se renforce : `-0,50`, `-1,49`,
`-7,32`.

**Ce n'est pas une tranche trouvee en fouillant, c'est une variable ordonnee.**
Elle se teste donc par une **tendance** — une seule statistique, aucune
multiplicite — et non en comparant des bacs.

- Test de score de la pente dans `logit(P) = logit(1/cote) + a + b·log(cote)`,
  l'ordonnee a l'origine restant un parametre de nuisance qui porte le deficit
  global deja mesure.
- Etat au 11/08 : **z = -1,813**, unilateral p = 0,0349, **bilateral
  p = 0,0698**. C'est le bilateral qui fait foi sur ces donnees-la : la
  direction y a ete **vue dans le tableau avant d'etre testee**, et prendre
  l'unilateral reviendrait a diviser le seuil apres avoir regarde.

**C'est precisement ce que ce fichier change.** En declarant la direction
**ici**, avant les donnees fraiches, l'unilateral redevient licite sur elles :
la direction n'est plus lue dans l'echantillon qui la teste. C'est la difference
exacte avec le pre-enregistrement retire, et elle est de nature — pas de degre.

**Le prior est externe.** Le biais favori-outsider est documente independamment
de ces donnees : sur les cotes longues la marge du book est plus elevee et le
prix surestime la probabilite. La direction n'a donc pas ete choisie parce
qu'elle arrangeait, elle etait prevue.

---

## 2. Le test — un seul

| Champ | Valeur |
| --- | --- |
| Hypothese | La pente du residu sur `log(cote)` est **negative** |
| Population | Anteriorite etablie, `picks.created_at < events.commence_time` |
| Donnees | **Fraiches uniquement** — voir §3 |
| Test | Score de la pente, **unilateral**, ordonnee a l'origine en nuisance |
| Seuil | α = 0,05 |
| Cible | **~137 selections** d'anteriorite etablie, pour 80 % de puissance |
| Regle d'arret | Au **compte de selections**, jamais en sessions |

Il n'y a pas d'hypothese secondaire, pas de sous-groupe prevu, pas de variante.
La transformation est fixee ici : `log(cote)`, et non la cote brute — les deux
donnaient un resultat voisin (0,0698 contre 0,0623), et laisser le choix ouvert
serait laisser deux tests la ou on en declare un.

---

## 3. La fenetre part de la garde, pas de l'ecriture

**Borne de depart : le 11/08/2026, mise en service de la garde d'anteriorite**
(migration 034). C'est la date a partir de laquelle une population est propre
**par construction** plutot que par filtrage.

Mais les selections deja nees entre cette mise en service et l'ecriture de ce
fichier — **5 au 11/08** — **ne comptent pas dans la fenetre** : leur direction
etait deja connue au moment ou elles ont ete produites. La fenetre s'ouvre sur
ce qui vient apres, et le compte se tient a partir de la.

**La cible se compte en selections d'anteriorite etablie, jamais en sessions.**
Le debit va changer avec la garde : elle refuse ce qui etait auparavant
enregistre sans motif, et le lot utile se reduira. Un compteur en sessions
cloturerait la fenetre sur un echantillon plus petit qu'annonce.

---

## 4. Regle d'echec

Si la pente ne se reproduit pas sur les donnees fraiches, **l'hypothese est
declaree non validee** et ce fichier n'est pas reecrit.

**Et si la pente echoue tandis qu'une autre tranche de cotes reussit, ce n'est
pas un resultat** : c'est le meme chemin parcouru une seconde fois. Une telle
tranche ne peut etre pre-enregistree que dans un fichier distinct, contre une
**troisieme** collecte, et le present echec reste inscrit.

Sans cette clause, un echec produirait mecaniquement le tour suivant — la
mecanique meme que le premier pre-enregistrement a servi a arreter.

---

## 5. Ce que le resultat vaudrait

**C'est le seul point de tout le chantier qui deboucherait sur une action
plutot que sur une mesure.** Le residu global dit « ces selections ont moins
reussi que leurs prix ne l'annoncaient » ; une pente confirmee dirait « et
principalement sur les cotes longues », ce qui se traduit immediatement.

Ordre de grandeur au 11/08 : les douze selections a cote >= 2.00 portent **47 %**
du deficit total.

Reserve a garder attachee : cette tranche fait `1/12` pour 5,40 payees, soit
p = 0,0082 et une **fragilite de 2**. Deux victoires l'effacent. C'est aussi
pourquoi elle ne se porte pas seule, et pourquoi c'est la **pente** qui est
pre-enregistree et non elle.

---

## 6. Avenant du 11/08/2026 — ce qui se mesure a la cloture

**Ajoute le jour meme, avant que la fenetre ait produit la moindre donnee, et il
ne touche a rien de ce qui engage** : ni l'hypothese (§1), ni le test (§2), ni la
fenetre (§3), ni la regle d'echec (§4). Il inscrit une **seconde mesure a faire
au meme moment**, et c'est le seul motif pour lequel ce fichier accepte un ajout.

Le bloc de retour d'experience du prompt est suspendu depuis le 11/08
(`FEEDBACK_SUSPENDED`). La suspension a une contrepartie qui se perd par inertie
si personne ne l'inscrit : **a la cloture de cette fenetre, le rallumer et
mesurer l'ecart.**

- **C'est le seul moment ou la question sera repondable.** « Le retour
  d'experience aide-t-il l'analyse, ou la fait-il boucler sur elle-meme » n'a
  jamais pu se poser : les trois sessions qui ont recu des taux les ont recus
  sans regime de comparaison propre. La fenetre qui s'ouvre est le **premier
  regime propre** de ce projet — garde d'anteriorite en service, bloc suspendu —
  donc le premier terme d'une comparaison qui vaille.
- **Si le bloc reste suspendu indefiniment, l'occasion passe** : il faudra alors
  une seconde fenetre propre pour la recreer, et le cout double.
- Ce qui se compare : le residu au prix, l'ordre des echelles et le taux de
  selection, sur la fenetre suspendue puis sur la fenetre rallumee. Aucune de
  ces trois grandeurs n'est le taux de reussite nu, qui n'est comparable a rien.

Les deux fenetres se ferment donc **au meme endroit** : quand la cible du §2 est
atteinte.

## 7. Resultat

*A completer une seule fois, quand la cible du §2 est atteinte.*

| Champ | Valeur |
| --- | --- |
| Date de cloture | — |
| Selections fraiches | — |
| z | — |
| p (unilateral) | — |
| Verdict | — |
