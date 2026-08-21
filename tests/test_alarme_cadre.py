"""L'alarme de cadre, et ce qu'elle doit avoir mesure **avant** la coupe.

**Les deux budgets du projet n'ont jamais vu un lot reel.** Ils vivent dans
`tests/`, s'appliquent a des fixtures de six et trois matchs, et rien ne les lit
a l'execution : c'est ce qui a laisse le cadre passer de 8 048 a 15 232 tokens en
dix jours sans qu'une ligne le signale.

Le piege que ces tests ferment est le suivant : apres la migration, une alarme
muette aura **deux causes indiscernables** — le cadre a fondu, ou l'alarme n'a
jamais mordu. La seule facon de les separer est de l'avoir fait tourner avant.
"""

from __future__ import annotations

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services.prompt import (
    FRAME_ALERT_MUTED,
    FrameAlert,
    frame_alert,
    frame_history,
)
from myassistantbet.services.thresholds import save as save_threshold


def _session(settings: Settings) -> int:
    """Une session, parce que `prompts` porte une cle etrangere vers elle."""
    db.execute("INSERT INTO sessions (created_at) VALUES (?)", (db.utcnow(),), settings=settings)
    row = db.query_one("SELECT MAX(id) AS id FROM sessions", settings=settings)
    return int(row["id"])


def _prompt(settings: Settings, fixed: int, session_id: int) -> None:
    """Archive un prompt dont le cadre pese `fixed` tokens."""
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at, "
        "                     blocks, fixed_tokens, block_tokens) "
        "VALUES (?, 'x.md.j2', '', ?, ?, 1, ?, 0)",
        (session_id, fixed, db.utcnow(), fixed),
        settings=settings,
    )


def test_l_alarme_porte_sur_le_cadre_et_jamais_sur_le_total(migrated: Settings) -> None:
    """**Un gros lot n'est pas un defaut.** Vingt-et-un blocs pesent legitimement
    21 707 tokens ; ce qui doit alerter est ce qui se paie une fois par prompt.

    Une alarme sur le total se declencherait sur la taille du lot, c'est-a-dire
    sur ce que l'utilisateur a choisi.
    """
    save_threshold("cadre_max", "5000", migrated)
    cadre = "cadre " * 200
    blocs = "\n".join(f"### M{index} · foot · X – Y · 18h\n" + "bloc " * 400 for index in range(20))

    alerte = frame_alert(cadre + "\n" + blocs, migrated)

    assert alerte.fixed < 5_000, "le cadre seul reste sous le seuil"
    assert not alerte.exceeded, "le poids des blocs ne doit pas declencher l'alarme"


def test_l_alarme_mord_quand_le_cadre_grossit(migrated: Settings) -> None:
    """**Le depassement se constate meme quand l'ecran se tait.** C'est la
    distinction qui compte : `exceeded` decrit le prompt, `visible` decrit ce que
    l'interface en montre."""
    save_threshold("cadre_max", "1000", migrated)

    alerte = frame_alert("cadre " * 2_000 + "\n### M1 · foot · X – Y · 18h\nbloc", migrated)

    assert alerte.exceeded


def test_la_ligne_se_tait_sans_que_la_mesure_s_arrete() -> None:
    """**Un signal toujours actif ne se distingue pas d'un signal absent.**

    A 20 depassements sur 20, la ligne paraitrait a chaque generation et
    deviendrait du decor — le defaut qu'elle existe pour corriger. Elle se coupe
    donc a l'ecran **jusqu'a la coupe du gabarit**, et rien d'autre ne s'arrete :
    `fixed_tokens` s'ecrit, `frame_history` compte, le journal avertit.

    Couper la mesure avec l'affichage ferait perdre l'« avant » que l'alarme
    vient d'etre livree pour se donner.
    """
    tue = FrameAlert(fixed=20_000, ceiling=1_000, muted=True)

    assert tue.exceeded, "le depassement reste un fait"
    assert not tue.visible
    assert tue.line == ""

    rallumee = FrameAlert(fixed=20_000, ceiling=1_000, muted=False)
    assert rallumee.visible
    assert "Cadre du prompt" in rallumee.line
    assert str(rallumee.ceiling) in rallumee.line


def test_l_etat_de_coupure_descend_dans_l_objet(migrated: Settings) -> None:
    """**Un champ, jamais une propriete qui irait lire la constante.**

    Relue a chaque acces, deux releves du meme prompt deviendraient
    indiscernables des qu'elle change, et l'etat d'exploitation serait invisible
    a la verification — le piege deja paye par `Feedback.suspended`.
    """
    save_threshold("cadre_max", "1000", migrated)

    assert frame_alert("cadre " * 2_000 + "\n### M1 · x\nbloc", migrated).muted is FRAME_ALERT_MUTED


def test_une_alarme_qui_ne_mord_pas_ne_dit_rien() -> None:
    """**Rien, et surtout pas « cadre normal ».** Une ligne rassurante a chaque
    generation deviendrait du decor, et c'est ce qui a fait manquer la derive.

    **Construit sans la coupure, et c'est indispensable** : passer par
    `frame_alert` rendrait une ligne vide de toute facon tant que l'affichage est
    suspendu, et le test passerait pour la mauvaise raison — un test mort qui en
    a l'air vivant.
    """
    assert FrameAlert(fixed=800, ceiling=10_000, muted=False).line == ""


def test_l_alarme_ne_refuse_jamais_un_prompt() -> None:
    """Une alarme, pas un budget : le depassement se lit sur l'objet et
    n'interrompt rien. Meme arbitrage qu'un seuil illisible qui revient au
    defaut — refuser de servir une page serait hors de proportion."""
    alerte = FrameAlert(fixed=99_000, ceiling=1_000)

    assert alerte.exceeded
    assert alerte.line, "le depassement se dit"


def test_l_historique_dit_combien_de_fois_l_alarme_a_mordu(migrated: Settings) -> None:
    """**C'est cette lecture qui rendra la coupe interpretable, et elle seule.**

    Sans elle, une alarme muette apres la migration se lira « le cadre a fondu »
    aussi bien que « elle n'a jamais mordu ».
    """
    save_threshold("cadre_max", "10000", migrated)
    session_id = _session(migrated)
    for cadre in (8_000, 12_000, 15_000):
        _prompt(migrated, cadre, session_id)

    releve = frame_history(migrated)

    assert releve.prompts == 3
    assert releve.exceeded == 2
    assert releve.worst == 15_000
    assert releve.share is not None and abs(releve.share - 2 / 3) < 1e-9


def test_sans_prompt_la_part_est_inconnue_et_jamais_nulle(migrated: Settings) -> None:
    """**Zero se lirait « aucun depassement », ce qui est l'inverse de la
    verite** : rien n'a ete mesure. Meme regle que le cout par bloc d'un prompt
    sans bloc, qui rend None plutot que zero."""
    releve = frame_history(migrated)

    assert releve.prompts == 0
    assert releve.share is None
    assert "n'a rien mesure" in releve.line


def test_la_fenetre_ecarte_les_prompts_anciens(migrated: Settings) -> None:
    """Le cadre a change de regime deux fois en deux semaines : une moyenne sur
    tout l'historique decrirait surtout le regime d'avant."""
    save_threshold("cadre_max", "10000", migrated)
    session_id = _session(migrated)
    for _ in range(5):
        _prompt(migrated, 20_000, session_id)
    for _ in range(3):
        _prompt(migrated, 1_000, session_id)

    releve = frame_history(migrated, window=3)

    assert releve.prompts == 3
    assert releve.exceeded == 0, "seuls les trois derniers comptent"


def test_le_seuil_est_regle_et_non_code_en_dur(migrated: Settings) -> None:
    """« A partir de quel cadre je veux etre prevenu » est une decision de
    l'utilisateur, au meme titre que les bandes de cote."""
    corps = "cadre " * 2_000 + "\n### M1 · x\nbloc"

    save_threshold("cadre_max", "50000", migrated)
    assert not frame_alert(corps, migrated).exceeded

    save_threshold("cadre_max", "1000", migrated)
    assert frame_alert(corps, migrated).exceeded
