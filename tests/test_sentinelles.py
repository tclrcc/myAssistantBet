"""Les drapeaux qui doivent se re-decider, et le test qui refuse de les oublier.

**Le precedent est la demonstration du probleme.** `FEEDBACK_SUSPENDED` porte
depuis des mois un commentaire disant que sa bascule ne se produira pas toute
seule — et il est toujours leve. Plus personne ne sait s'il l'est encore
volontairement ou seulement par oubli, et **les deux etats se ressemblent trait
pour trait** : c'est le defaut caracteristique de ce projet, applique a une
decision plutot qu'a une donnee.

Une chose a ne pas oublier ecrite dans un commentaire est une chose qui sera
oubliee. Ces tests la transforment en **chose qui refuse de l'etre** : ils
deviennent rouges quand la condition arrive, avec un message qui dit quoi faire.

**Ils ne demandent jamais de lever un drapeau** — ce serait decider a la place de
qui exploite. Ils demandent de **choisir**, et de reecrire la date ou l'etat avec
la raison si le choix est de ne rien changer.
"""

from __future__ import annotations

from datetime import date

from myassistantbet.services import framework
from myassistantbet.services.framework import FRAMEWORK_VERSION
from myassistantbet.services.history import (
    AUDITED_COLUMNS,
    DEFERRAL_TELL,
    FEEDBACK_SUSPENDED,
    FEEDBACK_SUSPENDED_DEFERRALS,
    FEEDBACK_SUSPENDED_REVIEW,
)
from myassistantbet.services.prompt import (
    ACTIVE_PRODUCER,
    FRAME_ALERT_MUTED,
    PRODUCER_PAYLOAD,
    PRODUCER_TEMPLATE,
)


def test_l_alarme_de_cadre_se_rallume_avec_la_coupe_du_gabarit() -> None:
    """**La condition est structurelle, pas calendaire.**

    L'alarme se tait parce qu'elle mord sur 20 prompts sur 20 : un signal
    toujours actif ne se distingue pas d'un signal absent. Cette raison
    **disparait avec le gabarit** — le cadre d'un payload se reduit a son
    en-tete de lot — et l'alarme redevient alors ce pour quoi elle existe :
    ce qui attrape une reprise de cadre.

    La laisser tue apres la bascule reviendrait a supprimer l'instrument au
    moment precis ou il recommence a dire quelque chose.
    """
    assert not (ACTIVE_PRODUCER == PRODUCER_PAYLOAD and FRAME_ALERT_MUTED), (
        "Le payload est devenu le producteur actif : remets FRAME_ALERT_MUTED a False. "
        "L'alarme s'etait tue parce qu'elle mordait sur tous les prompts ; cette raison "
        "vient de disparaitre avec le gabarit."
    )


def test_l_etat_de_migration_reste_l_un_des_deux_declares() -> None:
    """Un troisieme etat passerait au travers de la sentinelle sans un mot."""
    assert ACTIVE_PRODUCER in (PRODUCER_TEMPLATE, PRODUCER_PAYLOAD)


def _rendez_vous_echu(today: date | None = None) -> bool:
    return FEEDBACK_SUSPENDED and (today or date.today()) > date.fromisoformat(
        FEEDBACK_SUSPENDED_REVIEW
    )


def _question(reports: int) -> str:
    """Ce que le test demande, et la question change avec la pile.

    **Elle n'est jamais « faut-il lever ce drapeau »** : celle-la fait choisir
    entre lever et reporter, deux reponses qui supposent toutes deux un
    provisoire. Au troisieme report, elle cesse meme de proposer le report.
    """
    tete = (
        f"FEEDBACK_SUSPENDED est leve depuis le rendez-vous du {FEEDBACK_SUSPENDED_REVIEW}. "
        "La question n'est pas « faut-il le lever » mais « ce drapeau a-t-il jamais eu une "
        "condition de sortie ». "
    )
    if reports >= DEFERRAL_TELL:
        return tete + (
            f"Il a deja ete reporte {reports} fois : la liste dit ce que la date ne dit pas. "
            "Un drapeau sans condition de falsification n'est pas provisoire — c'est une "
            "decision de conception, et le traitement est de retirer le mot provisoire et "
            "de l'assumer. Reporter une quatrieme fois serait la mauvaise reponse."
        )
    return tete + (
        "Trois reponses valables : lever le drapeau ; retirer le mot provisoire si aucune "
        "condition de sortie n'existe ; ou **empiler** une entree dans "
        "FEEDBACK_SUSPENDED_DEFERRALS avec la raison, puis reecrire la date. Empiler et "
        "non remplacer : une date remplacee ne garde aucune trace."
    )


def test_la_suspension_du_retour_d_experience_se_re_decide(
    today: date | None = None,
) -> None:
    """**Un rendez-vous, et non une echeance qui leverait le drapeau.**

    Le test ne dit pas que la suspension a trop dure : il dit que personne ne
    l'a re-examinee depuis la date qu'elle porte.

    **Et il pose la question qui ouvre la troisieme branche.** « Faut-il le
    lever » fait choisir entre lever et reporter, et les deux supposent un
    provisoire ; « a-t-il jamais eu une condition de sortie » laisse place a la
    reponse que ce drapeau attend peut-etre — qu'il n'est pas provisoire du tout.

    **Il ne se declenche que si le drapeau est encore leve** : une fois la
    suspension retiree, il n'y a plus rien a re-decider.
    """
    assert not _rendez_vous_echu(today), _question(len(FEEDBACK_SUSPENDED_DEFERRALS))


def test_le_rendez_vous_devient_rouge_quand_la_date_passe() -> None:
    """**Le test de la sentinelle elle-meme.**

    Un rendez-vous qui ne se declencherait jamais serait pire qu'absent : il
    donnerait l'apparence d'un garde-fou. On verifie donc qu'il tombe une fois
    la date franchie — et qu'il se tait quand le drapeau est retire.
    """
    apres = date.fromisoformat(FEEDBACK_SUSPENDED_REVIEW).replace(year=2099)

    if FEEDBACK_SUSPENDED:
        try:
            test_la_suspension_du_retour_d_experience_se_re_decide(today=apres)
        except AssertionError:
            pass
        else:
            raise AssertionError("le rendez-vous ne se declenche pas : il ne garde rien")


def test_le_rendez_vous_porte_une_date_lisible() -> None:
    """Une date illisible ferait passer la sentinelle en erreur plutot qu'en
    echec, et le message qui dit quoi faire serait perdu."""
    assert date.fromisoformat(FEEDBACK_SUSPENDED_REVIEW)


def test_au_troisieme_report_la_question_cesse_de_proposer_le_report() -> None:
    """**La liste est le diagnostic, pas l'historique.**

    Un report peut suivre un evenement exterieur, deux peuvent etre une
    coincidence, trois disent que la question posee n'est pas la bonne : le
    drapeau n'attend pas un evenement, et n'en a jamais attendu. Le message cesse
    alors de presenter le report comme une reponse valable.

    Sans ce basculement, chaque report parait raisonnable pris seul — et c'est
    exactement ainsi qu'une decision de conception reste deguisee en provisoire.
    """
    tot = _question(DEFERRAL_TELL - 1)
    tard = _question(DEFERRAL_TELL)

    assert "empiler" in tot.lower(), "sous le seuil, le report reste une reponse valable"
    assert "reporter une quatrieme fois" in tard.lower()
    assert "decision de conception" in tard


def test_la_question_posee_ouvre_la_troisieme_branche() -> None:
    """« Faut-il le lever » fait choisir entre lever et reporter, et les deux
    supposent un provisoire. La question doit laisser place a la reponse que ce
    drapeau attend peut-etre : qu'il n'en est pas un."""
    for reports in (0, DEFERRAL_TELL):
        message = _question(reports)
        assert "condition de sortie" in message
        assert "faut-il le lever" not in message.replace("« faut-il le lever »", "")


def test_les_reports_s_empilent_et_portent_leur_raison() -> None:
    """Une date remplacee ne garde aucune trace. Chaque entree porte la date
    posee **et** la raison — sans elle, la pile compte sans rien dire."""
    for pose, raison in FEEDBACK_SUSPENDED_DEFERRALS:
        assert date.fromisoformat(pose)
        assert raison.strip(), f"report du {pose} sans raison ecrite"


# -- Le numero de cadre ------------------------------------------------------


def _divergence_du_cadre() -> str | None:
    """Ce que la lecture reproche au numéro déclaré, ou `None` si elle l'accorde.

    **La lecture se fait toujours**, quel que soit le producteur actif : un garde
    conditionné ne doit pas devenir un garde qui ne s'exécute plus, sans quoi il
    se serait tu pour deux raisons dont une seule est écrite.
    """
    lu = framework.evidence()
    if lu is None:
        return (
            "aucun cadre publié lisible et aucune preuve enregistrée : "
            "lance « uv run myassistantbet-cadre --relire » et commite deploy/cadre-lu.json"
        )
    if lu.version != FRAMEWORK_VERSION:
        return (
            f"le cadre lu déclare {lu.version} et la constante dit {FRAMEWORK_VERSION}. "
            "Deux écritures de la même chose ont divergé : soit le cadre a été publié "
            "et la constante n'a pas suivi, soit elle a été bumpée en avance. "
            f"Source lue : {lu.path}"
        )
    return None


def test_le_cadre_declare_n_est_audite_que_s_il_etiquette_quelque_chose() -> None:
    """**Le référent de cadre existait déjà, et ce n'est pas celui-ci.**

    `sessions.gabarit_sha` est une empreinte **mécanique** du gabarit rendu et
    `gabarit_version` le libellé de la décision qui l'a changé : les deux sont
    écrites par `save_prompt` sur ce qui produit vraiment. `FRAMEWORK_VERSION`,
    lui, suit la Skill — donc il étiquetait le mauvais sujet, et il n'étiquette
    plus rien depuis que `add_pick` a cessé de l'estampiller.

    L'équivalence est rouge **dans les deux sens**, et c'est ce qui en fait une
    sentinelle : auditer une colonne que rien ne remplit ferait crier au défaut
    sur le comportement voulu ; cesser de l'auditer le jour où le payload la
    réémet la laisserait se vider en silence.
    """
    audite = "framework_version" in {colonne.column for colonne in AUDITED_COLUMNS}

    assert audite == (ACTIVE_PRODUCER == PRODUCER_PAYLOAD), (
        f"le producteur actif est « {ACTIVE_PRODUCER} » et la colonne est "
        f"{'auditée' if audite else 'hors audit'}. Le payload émet "
        "`framework_version` dans ce qui part, le gabarit non : l'audit des "
        "colonnes muettes doit suivre celui des deux qui produit."
    )


def test_la_lecture_du_cadre_publie_revient_avec_le_payload() -> None:
    """**Le rouge s'est fermé parce que la question est répondue ailleurs, pas
    parce qu'elle a été tue.**

    Rappel de l'erreur du 21/08/2026 : `FRAMEWORK_VERSION` est passé à `1.4` sur
    une déclaration de publication alors que le cadre servi disait encore `1.3`.
    Le garde qui en est sorti compare la constante à une **lecture** du cadre
    publié — le fichier lisible fait foi, à défaut la preuve enregistrée par
    `myassistantbet-cadre --relire`.

    Ce que cette comparaison garde n'existe que si le numéro étiquette une
    sortie. Tant que le gabarit produit, il n'en étiquette aucune : la Skill a
    été publiée en 1.4 puis désactivée, le gabarit porte seul la méthode, et un
    écart entre deux copies dont aucune ne sert n'apprend rien. Le jour où
    `ACTIVE_PRODUCER` bascule, `build_payload` réémet le champ dans ce qui part
    et la lecture redevient le seul moyen de savoir sous quel cadre.

    **La lecture est faite dans tous les cas** : seule l'exigence est
    conditionnée, pas l'exécution.
    """
    ecart = _divergence_du_cadre()

    assert not (ACTIVE_PRODUCER == PRODUCER_PAYLOAD and ecart), (
        "Le payload est devenu le producteur actif : il émet `framework_version` "
        "dans ce qui part, donc le numéro déclaré étiquette de nouveau une sortie "
        f"et doit s'appuyer sur une lecture. {ecart}"
    )


def test_la_preuve_enregistree_ne_prend_le_relais_qu_a_defaut(tmp_path) -> None:
    """**L'ordre n'est pas négociable.** Un exemplaire lisible est la source ; la
    preuve n'est qu'un souvenir de lecture. Lui laisser la priorité rendrait le
    garde vert sur une machine qui a le vrai fichier sous les yeux et le
    contredit — exactement la situation du 21/08."""
    faux_home = tmp_path / "home"
    cadre = faux_home / ".claude" / "skills" / "myassistantbet-framework"
    cadre.mkdir(parents=True)
    (cadre / "SKILL.md").write_text("**Version 9.9.** cadre publié\n", encoding="utf-8")
    preuve = tmp_path / "cadre-lu.json"
    framework.record(framework.Published(version="1.3", sha256="x", path="ancien"), preuve)

    retenu = framework.evidence(home=faux_home, path=preuve)

    assert retenu is not None
    assert retenu.version == "9.9", "le fichier lu l'emporte sur le souvenir"


def test_sans_cadre_lisible_la_preuve_repond(tmp_path) -> None:
    """Un dépôt frais, une CI, une machine sans le plugin : la preuve versionnée
    est ce qui reste, et elle porte sa date de lecture."""
    preuve = tmp_path / "cadre-lu.json"
    framework.record(
        framework.Published(version="1.3", sha256="x", path="p", read_at="2026-08-21T21:53:42Z"),
        preuve,
    )

    retenu = framework.evidence(home=tmp_path / "vide", path=preuve)

    assert retenu is not None
    assert retenu.version == "1.3"
    assert retenu.read_at == "2026-08-21T21:53:42Z", "une preuve dit quand elle a ete faite"


def test_sans_lecture_ni_preuve_le_garde_est_rouge(tmp_path) -> None:
    """**Un garde qui se tait quand il ne peut pas vérifier est indiscernable
    d'un garde qui a vérifié.** C'est le défaut caractéristique du projet,
    appliqué au dispositif de vérification lui-même."""
    assert framework.evidence(home=tmp_path / "vide", path=tmp_path / "absente.json") is None


def test_une_preuve_illisible_vaut_une_preuve_absente(tmp_path) -> None:
    """La traiter autrement ferait passer un fichier cassé pour une vérification."""
    casse = tmp_path / "cadre-lu.json"
    casse.write_text("{ pas du json", encoding="utf-8")

    assert framework.recorded(casse) is None
