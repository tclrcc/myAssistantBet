"""Le bloc de donnees : ce qu'il porte, et ce qu'il ne peut pas confondre."""

from __future__ import annotations

import json

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import board
from myassistantbet.services.attribution import UNKNOWN_LEVEL
from myassistantbet.services.confidence import is_claim, read_blocks
from myassistantbet.services.manual import build, save
from myassistantbet.services.payload import (
    ATTRIBUT_COLUMNS,
    ORIGIN,
    SECTIONS,
    FactAudit,
    audit_facts,
    build_payload,
)


def _session(settings: Settings, cotes: str = "Lyon 2.10\nNice 3.40") -> int:
    event_id = save(
        build(
            "football",
            "Amical",
            "Lyon",
            "Nice",
            "2099-01-01",
            "20:45",
            cotes,
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board.toggle_selection(event_id, True, settings)


def test_le_lot_est_un_objet_json_unique(migrated: Settings) -> None:
    """**Jamais une suite d'objets.** Le scan du lecteur de blocs avance de `{`
    en `{` et un objet qui se relit fait sauter tout ce qu'il contient : la
    racine avale ses matchs, et c'est elle qui protege les objets internes."""
    texte = build_payload(_session(migrated), migrated).dumps()

    charge = json.loads(texte)
    assert isinstance(charge, dict)
    assert texte.strip().startswith("{") and texte.strip().endswith("}")


def test_la_racine_et_chaque_match_declarent_leur_emetteur(migrated: Settings) -> None:
    """Le discriminant n'est pas decoratif : sans lui, un objet-match recopie
    dans une reponse serait lu comme un bloc de confiance."""
    charge = build_payload(_session(migrated), migrated).data

    assert charge["origine"] == ORIGIN
    assert charge["matchs"], "sans match, le test ne prouve rien"
    for match in charge["matchs"]:
        assert match["origine"] == ORIGIN


def test_un_objet_match_n_est_jamais_lu_comme_un_bloc_de_confiance(
    migrated: Settings,
) -> None:
    """**Le garde-fou par construction, et il vaut plus que le renommage.**

    `is_claim` reconnait un bloc a sa seule forme quand la cloture manque. Un
    objet-match colle dans une reponse doit etre ignore proprement — ni bloc, ni
    rejet. Un rejet compte serait deja la panne : le compte des blocs divergerait
    de celui des lignes, et le lot perdrait ses crans.
    """
    charge = build_payload(_session(migrated), migrated).data
    match = charge["matchs"][0]

    assert not is_claim(match)
    lecture = read_blocks(f"voici mon analyse\n\n{json.dumps(match, ensure_ascii=False)}\n\nfin")
    assert lecture.claims == []
    assert lecture.rejects == [], "un rejet compte couterait les crans du lot"


def test_le_lot_entier_colle_ne_produit_ni_bloc_ni_rejet(migrated: Settings) -> None:
    """Le cas le plus probable : l'utilisateur recolle tout le bloc avec sa
    reponse."""
    texte = build_payload(_session(migrated), migrated).dumps()

    lecture = read_blocks(f"```json\n{texte}\n```\n\nvoici mon analyse")

    assert lecture.claims == []
    assert lecture.rejects == []


def test_les_cotes_sont_colonnaires_et_datees_une_fois(migrated: Settings) -> None:
    """Densite d'un tableau, un seul format, un seul parseur. `releve_le` decrit
    le releve, pas chaque prix."""
    charge = build_payload(_session(migrated), migrated).data
    cotes = charge["matchs"][0]["cotes"]

    assert cotes["colonnes"] == ["marche", "selection", "cote", "source"]
    assert cotes["lignes"], "le lot porte des cotes"
    for ligne in cotes["lignes"]:
        assert len(ligne) == len(cotes["colonnes"])
    assert "releve_le" in cotes


def test_chaque_attribut_porte_ses_trois_mentions(migrated: Settings) -> None:
    """Un fait sans source, sans date et sans niveau ne se juge pas.

    **En colonnaire comme les cotes** : cinq noms de champs repetes sur une
    vingtaine d'entrees par match, quand le cout par match decide si la migration
    a un argument.
    """
    charge = build_payload(_session(migrated), migrated).data

    for match in charge["matchs"]:
        attributs = match["attributs"]
        assert attributs["colonnes"] == list(ATTRIBUT_COLUMNS)
        for ligne in attributs["lignes"]:
            assert len(ligne) == len(ATTRIBUT_COLUMNS)
            assert ligne[ATTRIBUT_COLUMNS.index("niveau")] <= UNKNOWN_LEVEL


def test_le_lot_annonce_les_sections_qu_il_attend(migrated: Settings) -> None:
    """**La seule trace de cadre qui subsiste**, reduite a une liste de noms.

    Elle existe parce que l'audit des sections deduit ce qui etait demande en
    cherchant les motifs du gabarit dans le corps du prompt : sans gabarit, il
    conclurait « rien n'etait demande », donc « rien a reclamer ».
    """
    charge = build_payload(_session(migrated), migrated).data

    assert charge["sections_attendues"] == list(SECTIONS)


def test_le_cadre_sous_lequel_le_lot_est_rendu_est_consigne(migrated: Settings) -> None:
    """Sans lui, une analyse archivee ne se relit plus contre les regles en
    vigueur au moment ou elle a ete produite."""
    charge = build_payload(_session(migrated), migrated).data

    assert charge["framework_version"]


def test_la_collecte_ne_descend_pas_dans_les_attributs(migrated: Settings) -> None:
    """**Ce n'est pas un fait sur le match** : la densite mesure ce que la
    collecte a rapporte. « 0 sur 25 » lu comme une propriete de la rencontre
    dirait l'inverse de ce que la ligne existe pour dire."""
    charge = build_payload(_session(migrated), migrated).data

    assert "densite" in charge["collecte"]
    for match in charge["matchs"]:
        assert "densite" in match["collecte"]
        assert all(ligne[0] != "Densite" for ligne in match["attributs"]["lignes"])


def test_une_session_vide_rend_un_lot_sans_match(migrated: Settings) -> None:
    """Un lot vide est un lot, pas une erreur — et il le dit."""
    db.execute("INSERT INTO sessions (created_at) VALUES (?)", (db.utcnow(),), settings=migrated)
    session_id = int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=migrated)["id"])

    charge = build_payload(session_id, migrated).data

    assert charge["nb_matchs"] == 0
    assert charge["matchs"] == []
    assert charge["sports"] == []


def test_un_bloc_de_confiance_reste_lu_malgre_le_garde_fou(migrated: Settings) -> None:
    """**Le garde-fou ne doit pas fermer le canal retour.**

    `origine` ecarte un bloc de donnees ; un vrai bloc de confiance n'en porte
    pas et doit continuer d'entrer. Sans ce test, l'exclusion pourrait avaler
    les deux et le defaut ne se verrait qu'au prochain import.
    """
    bloc = json.dumps(
        {"match": "Lyon – Nice", "source_level": "1", "faits": [], "confiance": 4},
        ensure_ascii=False,
    )

    lecture = read_blocks(f"```conf\n{bloc}\n```")

    assert len(lecture.claims) == 1
    assert lecture.rejects == []


def test_le_payload_porte_autant_de_faits_que_le_rendu_texte(migrated: Settings) -> None:
    """**Controle amont du protocole de comparaison.**

    Le cas qui l'a fait ecrire est reel : un appel qui perdait la cle du
    fournisseur rendait un bloc de tennis a 10 attributs au lieu de 21, **sans
    qu'aucune erreur ne se leve**. Le test de comparaison l'aurait pris pour une
    faiblesse de la migration — tous ses criteres portent sur la sortie, et aucun
    ne voit la cause.
    """
    audits = audit_facts(_session(migrated), migrated)

    assert audits, "sans match, le controle ne prouve rien"
    ecarts = [audit.line for audit in audits if audit.gap]
    assert ecarts == [], f"le payload et le rendu texte divergent : {ecarts}"


def test_le_controle_amont_voit_un_bloc_appauvri(migrated: Settings) -> None:
    """**Un controle qui ne se declencherait jamais donnerait l'apparence d'un
    controle.** On lui montre donc un match dont les faits ont ete ampute."""
    session_id = _session(migrated)
    audits = audit_facts(session_id, migrated)
    complet = audits[0]

    ampute = FactAudit(repere=complet.repere, texte=complet.texte + 11, payload=complet.payload)

    assert ampute.gap == -11
    assert "-11" in ampute.line


# -- Les lecteurs de `prompts.body` apprennent la forme, sans perdre l'ancienne --


def test_un_corps_qui_commence_par_une_accolade_est_un_payload() -> None:
    """**La bascule se lit sur la forme, et rien d'autre.** Aucune colonne,
    aucune migration : les corps archives restent lisibles par le chemin qui les
    a produits."""
    from myassistantbet.services.ingestion import is_payload

    assert is_payload('{"origine": "myassistantbet"}')
    assert is_payload('\n  {"origine": "x"}')
    assert not is_payload("# SESSION D'ANALYSE\n\n### M1 · FOOT · …")
    assert not is_payload("")


def test_le_decoupage_du_cout_ne_met_pas_un_payload_tout_en_cadre(
    migrated: Settings,
) -> None:
    """**L'inverse exact de la verite, sur la mesure qui juge la coupe.**

    Le decoupage cherche des en-tetes `### M1 · …` ; un payload n'en porte
    aucun, donc la lecture d'origine rendait « tout en cadre » — zero bloc, cout
    fixe maximal — au moment precis ou l'on veut montrer que le cadre a disparu.
    """
    from myassistantbet.services.prompt import split_cost

    corps = build_payload(_session(migrated), migrated).dumps()

    cout = split_cost(corps)

    assert cout.fixed == 0, "un payload n'a pas de cadre"
    assert cout.blocks == 1
    assert cout.block_tokens == cout.tokens


def test_un_prompt_garde_son_decoupage_d_origine() -> None:
    """La forme d'avant ne se perd pas : c'est la moitie du contrat."""
    from myassistantbet.services.prompt import split_cost

    cout = split_cost("cadre " * 50 + "\n### M1 · FOOT · X – Y · 18h\nbloc\n## SORTIE ATTENDUE\nz")

    assert cout.blocks == 1
    assert cout.fixed > 0


def test_les_sections_attendues_se_lisent_dans_le_payload(migrated: Settings) -> None:
    """**Sans gabarit, les motifs ne se trouvent nulle part** et l'audit
    conclurait « rien n'etait demande », donc « rien a reclamer ». C'est le
    defaut que ce module existe pour corriger, retourne contre lui."""
    from myassistantbet.services.sections import SECTIONS as LECTEURS

    corps = build_payload(_session(migrated), migrated).dumps()

    for section in LECTEURS:
        assert section.asks(corps), f"« {section.label} » devrait etre reclamee"


def test_le_payload_n_ecrit_jamais_la_chaine_du_canal_retour(migrated: Settings) -> None:
    """**La collision fermee par construction.** `confidence.OPEN_KEY` cherche
    cette chaine dans le collage : un payload recolle avec la reponse ferait
    passer la ligne pour « illisible » plutot qu'« absente », et le projet a
    separe ces deux etats exprès."""
    corps = build_payload(_session(migrated), migrated).dumps()

    assert "dossiers_ouverts" not in corps


def test_les_deux_ecritures_du_compte_de_matchs_ne_divergent_pas() -> None:
    """`prompt` et `history` portent chacun l'expression qui lit `nb_matchs` :
    `prompt` importe `history`, donc l'inverse fermerait le cycle. Deux
    ecritures de la meme regle divergent — celle-ci ne le pourra pas en
    silence."""
    from myassistantbet.services.history import PAYLOAD_MATCHS as COTE_HISTORY
    from myassistantbet.services.prompt import PAYLOAD_MATCHS as COTE_PROMPT

    assert COTE_PROMPT.pattern == COTE_HISTORY.pattern
