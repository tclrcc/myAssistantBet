# Phase 1 — addendum 3 : le chiffre borne sur le regime actuel

---

## 1. Le residu du regime actuel — sessions 8-22

C'est le seul chiffre qui decrit l'application telle qu'elle tourne.

| Section C, **anterieures, sessions 8-22** | |
| --- | ---: |
| n | **281** |
| couverture overround | **84,3 %** |
| ecarts `price_real` observes | 108 |
| **residu brut** | **−4,82** (−1,72 pts/sel) |
| **residu corrige** | **−4,49** (**−1,60 pts/sel**) |
| **IC 95 %, trois sources** | **[−20,67 ; +11,15]** |
| par selection | [−7,36 ; +3,97] pts |
| P(residu ≥ 0) | **0,285** |
| **zero dans l'intervalle** | **OUI** |

**Sur le regime actuel, aucun deficit n'est etabli, et il ne s'en approche pas** :
`P(residu ≥ 0) = 0,285`, contre 0,063 sur la base entiere. Le point estime vaut
−1,60 point par selection, soit **un tiers** du −4,59 publie.

Meme mesure en section C-bis (n=90, couverture 85,6 %) : corrige **−0,91**,
IC **[−8,82 ; +7,24]**, `P(≥0) = 0,404`. Zero dedans.

### Une attente dementie, et elle merite d'etre dite

**L'intervalle n'est pas « nettement plus resserre ». Il est plus large par
selection** — 11,33 points contre 10,16 sur les 349.

Moins d'imputation reduit bien une composante de variance ; mais passer de 349 a
281 selections augmente la composante **dominante**, et c'est elle qui gagne.
L'addendum 2 l'avait deja mesure : la variance des issues pese 30,08 de largeur
quand les corrections pesent 1,33. **Reduire l'imputation ne peut pas resserrer
un intervalle que l'imputation ne fait presque pas bouger.**

Ce que le resserrement du perimetre apporte n'est donc **pas de la precision,
c'est de la validite** : le chiffre porte sur une population homogene et
mesurable, au lieu de melanger deux regimes dont l'un ne se mesure pas.

---

## 2. Le deficit des sessions 2-7 n'est pas attribuable

| Section C, **anterieures, sessions 2-7** | |
| --- | ---: |
| n | 68 |
| **couverture overround** | **0 %** |
| ecarts `price_real` observes | **2** |
| residu brut | −11,19 (−16,45 pts/sel) |
| residu corrige (imputation totale) | −9,03 (−13,27 pts/sel) |
| IC 95 % | **[−16,47 ; −1,58]** |
| P(residu ≥ 0) | 0,009 |
| zero dans l'intervalle | **NON** |

**C'est la seule population du jeu de donnees ou le deficit franchit le seuil —
et c'est precisement celle ou il ne signifie rien.**

L'attendu y est calcule sur `picks.price`, c'est-a-dire **le prix du bloc**.
Trois faits se cumulent :

1. **Aucun marche n'est fige** : l'overround y est integralement impute depuis
   d'autres sessions, et rien ne garantit qu'il s'y applique.
2. **`price_real` n'y existe pratiquement pas** — 2 paires sur 68. L'ecart moyen
   de −6,8 % mesure ailleurs ne peut ni etre verifie, ni etre exclu.
3. **La qualite des prix de bloc y etait au plus bas** : `market_key` n'existait
   pas, la garde d'anteriorite non plus, et 19,4 % des selections de l'epoque
   sont tardives.

**Une part inconnue du −16,45 est donc de l'erreur de prix et non de la
selection.** La direction est connue — un prix de bloc optimiste creuse le
deficit — mais **l'ampleur n'est pas decomposable**, et elle ne le sera jamais :
l'etat du marche de cette semaine-la n'existe plus nulle part.

**Regle a tenir : ce chiffre ne caracterise pas le cadre.** Il se rapporte en
historique ventile, avec sa couverture a 0 %, et jamais comme une mesure de ce
que vaut la methode.

---

## 3. L'amelioration 2-7 → 8-22 n'est attribuable a aucune cause

Le residu passe de −16,45 a −1,72 points par selection. **Aucune phrase du type
« X a corrige le deficit » ne peut etre ecrite avec ces donnees.**

Quatre changements se chevauchent sur la meme fenetre de vingt jours :

| Date | Changement |
| --- | --- |
| session 8 (11/08) | `prompt_odds` entre en service — le marche est fige |
| 11/08 18:52 | migration 033 — `market_key` figee a l'ecriture |
| 17/08 | migration 053 — la garde d'anteriorite cesse de refuser et marque |
| 11 → 24/08 | le taux de tardives passe de **19,4 %** a **4,0 %** |

Ils sont **confondus** : aucun n'est isole d'un autre, et il n'existe aucune
periode ou l'un a bouge sans les autres. S'y ajoutent les changements de gabarit
— le journal en compte treize en quinze jours — qui n'ont laisse aucune borne
exploitable.

**Le plus tentant est le plus faux** : attribuer l'amelioration a la garde
d'anteriorite. Elle est arrivee **le 17/08**, six jours *apres* le debut de la
periode amelioree, et elle **ne refuse rien**. Le sens de la causalite est
d'ailleurs inverse de l'intuition — le taux de tardives a chute *quand la garde a
cesse de refuser*.

Ce qui se dit sans se tromper : **deux regimes coexistent dans la base, ils
different fortement, et le premier n'est pas mesurable.** Rien de plus.

---

## 4. Deux P0 d'affichage — correctifs minimaux et tests de non-regression

Ces deux cartes sont consultees **au moment de decider**. Elles induisent en
erreur maintenant.

> Rappel de perimetre : les phases 0 a 5 sont en lecture seule. Ce qui suit est
> **propose**, rien n'est applique.

### P0-1 — la carte « par cran calcule » compte 96 % d'artefact

**Constat.** `by_confidence_computed` (`history.py:5644`) ne filtre que sur
`confidence_computed IS NOT NULL`. Le cran 1 y affiche **n = 140 a 53,5 %**, dont
**134 sont des crans forces** par un defaut de collage. Six sont reels.

Deux autres lectures de la meme colonne connaissent la distinction — `_override`
(`history.py:5213`) et `changelog` — et l'appliquent. C'est le motif que le depot
documente comme le plus couteux : **deux ecritures que rien n'oblige a rester
d'accord.**

**Le test d'abord** — `tests/test_history.py` :

```python
def test_cran_calcule_ignore_les_defauts_de_collage(tmp_path):
    """Un cran force par un collage perdu n'est pas une observation.

    Il ne dit rien du modele : la question ne lui a pas ete transmise. Le
    compter fait lire un artefact de collage comme un taux de reussite.
    """
    session = _session_avec(
        # un cran 1 REEL : la recherche a eu lieu et n'a rien donne
        pick(confidence_computed=1, research_override_cause="sans_fait", result="win"),
        # un cran 1 FORCE : la ligne `dossiers_ouverts` n'a jamais ete collee
        pick(confidence_computed=1, research_overridden=1,
             research_override_cause="ligne_absente", result="win"),
    )
    cran1 = _ligne(history.analysis(session).by_confidence_computed, "1")
    assert cran1.total == 1, "le cran force ne doit pas entrer dans le compte"
```

**Le correctif**, mot pour mot celui de `_override` :

```python
for row, result in zip(rows, results, strict=True)
if _column(row, "confidence_computed") is not None
and not is_collection_fault(_column(row, "research_override_cause"))
and not is_unknown_cause(_column(row, "research_override_cause"))
```

**Effet mesure** : le cran 1 passe de **140 a 6** (5 tranchees), donc **sous
`minimum_rows`** — il sera palie et annonce comme maigre, ce qui est exactement
la lecture juste. Les crans 2 a 5 ne bougent d'aucune ligne.

**Ce que le correctif ne doit pas faire** : masquer les 134. Elles ont deja leur
compte dans `Override`, avec leur cause. Les retirer de la carte des **taux** ne
les retire pas de la mesure des **defauts de collecte** — ce sont deux questions,
et c'est tout l'objet de la separation.

### P0-2 — le residu de tete publie un global non ventile

**Constat.** `report.residual` (`history.py:4396`) porte toute la population a
anteriorite etablie, et `stats.html:122` le rend en `.tile-hero` — le chiffre le
plus visible de la page. Il vaut **−16,00 sur 349**, dont **−11,19 viennent de
68 selections dont le marche n'a jamais ete fige**.

**Le test d'abord** :

```python
def test_le_residu_de_tete_est_ventile_par_regime(tmp_path):
    """Un residu global melange deux regimes dont un n'est pas mesurable.

    Les sessions sans releve fige n'ont ni overround, ni cote obtenue : leur
    attendu repose sur le seul prix du bloc. Les additionner au reste fait
    lire comme une mesure ce qui est en partie une erreur de prix.
    """
    session = _session_avec(...)   # une selection figee, une non figee
    a = history.analysis(session)
    assert a.residual_by_regime, "la ventilation doit exister"
    fige = _ligne(a.residual_by_regime, "marche fige")
    assert fige.settled == 1
    assert a.residual.settled == 2, "le global reste rendu, a cote"
```

**Le correctif minimal**, et il **n'invente aucune frontiere** : la ventilation
se pose sur un fait deja en base — la session porte-t-elle un releve dans
`prompt_odds` ?

```python
#: Le residu, ventile selon que le marche a ete fige ou non. Ce n'est pas un
#: decoupage temporel invente apres coup : `prompt_odds` porte le releve ou il
#: ne le porte pas, et sans lui l'attendu repose sur le seul prix du bloc.
#: 20,3 % du volume est dans ce cas, definitivement.
report.residual_by_regime = _residual_rows(
    [(("fige" if _marche_fige(row) else "non fige"),
      "marché figé" if _marche_fige(row) else "marché non figé — attendu non vérifiable",
      row) for row in prices],
    minimum=report.minimum_rows,
)
```

**Ce qui n'est pas propose, et pourquoi.** Calculer l'overround **dans
l'application** pour corriger le residu affiche : la ligne entre marge et
devigging est trop fine pour etre franchie dans un correctif d'affichage, et
`SPEC.md` §9 interdit le second. La ventilation, elle, ne calcule rien de neuf —
elle **separe** ce qui ne se compare pas.

---

## 5. L'ecart `price_real` se concentre bien sur les marches « a relever »

### La mesure

| Categorie | Section | Paires | **Ecart moyen** | Mini | Plus basses |
| --- | --- | ---: | ---: | ---: | ---: |
| **marche « a relever »** | C | **98** | **−7,11 %** | −27,23 % | **96 / 98** |
| marche « a relever » | C-bis | 7 | −6,50 % | −12,03 % | 6 / 7 |
| h2h ou non resolu | C | 25 | **−3,54 %** | −16,82 % | 21 / 25 |
| h2h ou non resolu | C-bis | 2 | −5,53 % | −8,54 % | 2 / 2 |

**L'ecart est deux fois plus grand sur les marches « a relever », et il est
quasi unanime : 96 des 98 cotes obtenues sont plus basses.**

Par book de reference identifie : `pinnacle` **n=80, −6,58 %** (mediane −6,29 %),
les trois autres sous dix paires chacun.

### Ce n'est pas de l'execution, c'est une difference de marge — verifie a moitie

Si l'ecart venait de la seule difference de marge entre le book de reference et
le book principal, il vaudrait `(1 + ovr_ref) / (1 + ovr_principal) − 1` :

| Sport, sur `h2h` | Betclic | Pinnacle | **Predit** | **Observe** | n |
| --- | ---: | ---: | ---: | ---: | ---: |
| football | 8,21 % | 4,27 % | **−3,64 %** | **−3,97 %** | 14 |
| tennis | 4,43 % | 3,36 % | **−1,02 %** | **−3,16 %** | 10 |

**Le football colle ; le tennis non** — l'ecart y est trois fois le predit. Sur
l'agregat des deux, observe −3,63 % contre predit −3,64 %, **et cette coincidence
est fortuite** : elle vient de la compensation de deux ecarts opposes. A dix et
quatorze paires, aucune des deux lignes ne conclut seule.

### Ce que ca implique, en trois points

1. **Au football au moins, l'ecart n'est pas une perte d'execution mais une
   erreur de source de prix.** L'attendu oppose a la selection est un prix de
   book sharp, structurellement plus genereux qu'un prix de detail francais.
2. **Corrigeable en amont, et c'est le point le plus actionnable de la phase 1** :
   ce que le bloc devrait porter pour ces marches n'est pas le prix Pinnacle,
   c'est le prix Pinnacle **annonce comme non obtenable**, ce que la mention
   `(ref.)` fait deja — ou, mieux, le prix releve chez le book principal.
3. **Marge implicite de Betclic sur les marches profonds, DEDUITE** de l'ecart de
   −7,01 % : environ **11,4 % sur les handicaps, 11,7 % sur les totaux
   alternatifs, 13,9 % sur les totaux**. C'est une **deduction sous hypothese**,
   pas une mesure — Betclic ne sert aucune de ces lignes dans notre collecte,
   donc son overround y est structurellement inobservable. Elle est plausible :
   un book de detail elargit sa marge sur les marches derives.

> **Consequence pour la phase 3** : le residu des 236 selections « a relever »
> est calcule contre un prix qui n'etait pas obtenable, et l'ecart mesure y vaut
> **le double** de celui du h2h. Elles ne se comparent pas aux selections cotees
> chez le book principal, et les melanger dans un residu unique melange deux
> qualites de prix.

---

## Ce que cet addendum change

| Chiffre a citer | Valeur |
| --- | --- |
| **residu du regime actuel** (C, anterieures, sessions 8-22, n=281) | **−4,49 · IC [−20,67 ; +11,15] · P(≥0)=0,285 · zero dedans** |
| residu section C-bis, meme regime (n=90) | −0,91 · IC [−8,82 ; +7,24] · zero dedans |
| residu sessions 2-7 (n=68) | −9,03 · IC [−16,47 ; −1,58] · **non attribuable** |
| residu sur les 349 | **a ne plus rapporter seul** — historique ventile |
