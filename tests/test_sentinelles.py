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

from myassistantbet.services.history import (
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
