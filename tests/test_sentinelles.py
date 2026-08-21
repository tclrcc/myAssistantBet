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

from myassistantbet.services.history import FEEDBACK_SUSPENDED, FEEDBACK_SUSPENDED_REVIEW
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


def test_la_suspension_du_retour_d_experience_se_re_decide(
    today: date | None = None,
) -> None:
    """**Un rendez-vous, et non une echeance qui leverait le drapeau.**

    Le test ne dit pas que la suspension a trop dure : il dit que personne ne
    l'a re-examinee depuis la date qu'elle porte. Garder le drapeau est une
    reponse parfaitement valable — elle demande seulement d'etre ecrite, avec
    sa raison et une nouvelle date.

    **Il ne se declenche que si le drapeau est encore leve** : une fois la
    suspension retiree, il n'y a plus rien a re-decider et le rendez-vous n'a
    plus d'objet.
    """
    echeance = date.fromisoformat(FEEDBACK_SUSPENDED_REVIEW)
    aujourd_hui = today or date.today()

    assert not (FEEDBACK_SUSPENDED and aujourd_hui > echeance), (
        f"FEEDBACK_SUSPENDED est leve depuis le rendez-vous du {FEEDBACK_SUSPENDED_REVIEW}. "
        "Deux reponses valables : lever le drapeau, ou reecrire "
        "FEEDBACK_SUSPENDED_REVIEW avec la raison de le garder. Ne pas repousser la date "
        "sans ecrire la raison — c'est ainsi qu'un provisoire devient permanent."
    )


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
