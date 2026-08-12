"""Repos et charge d'un joueur, calcules sur nos propres lignes."""

from __future__ import annotations

from datetime import date

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import tennis_load

TOURNOI = "tennis_atp_us_open"


def _competition(settings: Settings) -> int:
    row = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (TOURNOI,), settings=settings
    )
    return int(row["id"])


def _match(settings: Settings, home: str, away: str, when: str) -> None:
    competition_id = _competition(settings)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, 'api', ?)",
        (sport["id"], competition_id, home, away, when, db.utcnow()),
        settings=settings,
    )


def test_le_repos_se_compte_sur_les_tours_precedents(migrated: Settings) -> None:
    """L'information dormait deja en base : les tours precedents du meme
    tournoi ont ete scannes les jours d'avant. L'analyse allait la chercher a
    la main, match par match."""
    _match(migrated, "Fils", "Svajda", "2026-08-05T18:00:00Z")
    _match(migrated, "Navone", "Vacherot", "2026-08-04T18:00:00Z")

    lignes = tennis_load.lines(
        "Fils", "Navone", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes == [
        (
            "Repos",
            "Fils 48 h (2 j. tournoi, depuis 05/08 18:00 UTC)\n"
            "Navone 72 h (3 j. tournoi, depuis 04/08 18:00 UTC)",
        )
    ]


def test_le_nombre_de_tours_n_accompagne_plus_le_repos(migrated: Settings) -> None:
    """Il comptait les apparitions **scannees**, pas les matchs joues. Sur un
    tournoi dont les premiers jours precedent notre fenetre, il en manque :
    constate en reel, le bloc creditait Michelsen d'un tour la ou l'ATP lui en
    donne deux. La ligne « Tour » dit desormais ou en est le tournoi, et elle le
    dit juste — ce compte-la n'avait plus de raison d'etre.
    """
    _match(migrated, "Fils", "A", "2026-08-03T18:00:00Z")
    _match(migrated, "B", "Fils", "2026-08-05T18:00:00Z")

    lignes = tennis_load.lines(
        "Fils", "Inconnu", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes == [("Repos", "Fils 48 h (2 j. tournoi, depuis 05/08 18:00 UTC)")]


def test_un_joueur_sans_tour_precedent_ne_produit_rien(migrated: Settings) -> None:
    """Ecrire « 0 tour » laisserait croire a une entree en lice alors qu'on ne
    sait simplement pas : le tournoi peut n'avoir ete scanne que ce jour-la."""
    assert (
        tennis_load.lines(
            "Inconnu", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated
        )
        == []
    )


def test_un_autre_tournoi_ne_compte_pas(migrated: Settings) -> None:
    """La charge se mesure dans l'epreuve en cours, pas sur la saison."""
    autre = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_wta_us_open'", settings=migrated
    )
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, 'Fils', 'X', '2026-08-05T18:00:00Z', 'api', ?)",
        (sport["id"], int(autre["id"]), db.utcnow()),
        settings=migrated,
    )

    assert (
        tennis_load.lines("Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated)
        == []
    )


def test_un_match_trop_ancien_ne_compte_pas(migrated: Settings) -> None:
    """Au-dela de dix jours, c'est une autre semaine : le repos ne dit plus
    rien de la fraicheur."""
    _match(migrated, "Fils", "A", "2026-07-01T18:00:00Z")

    assert (
        tennis_load.lines("Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated)
        == []
    )


def test_les_accents_et_la_casse_ne_separent_pas_un_joueur(migrated: Settings) -> None:
    """Le fournisseur ecrit le meme joueur de la meme facon d'un tour a
    l'autre, mais la casse peut varier. Aucun rapprochement flou en revanche :
    deux joueurs differents ne doivent jamais partager un parcours."""
    _match(migrated, "Fabian Marozsán", "A", "2026-08-05T18:00:00Z")

    lignes = tennis_load.lines(
        "Fabian Marozsan", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes and lignes[0][1].startswith("Fabian Marozsan 48 h")


def test_le_repos_se_compte_en_journees_de_tournoi(migrated: Settings) -> None:
    """Le defaut constate en reel sur Montreal. Un match de la session du soir
    part a 01h du matin a Paris : sa date civile est celle du lendemain, et le
    repos calcule dessus perdait un jour d'un cote et en gagnait un de l'autre.

    Le bloc donnait van de Zandschulp a 1j et Paul a 3j la ou l'ATP date leurs
    deux matchs precedents du meme mercredi. Regroupes en journees de tournoi,
    les deux tombent sur le meme compte.
    """
    # Session du soir du 5 aout a Montreal : 23h10 UTC, soit 01h10 a Paris le 6.
    _match(migrated, "Paul", "Royer", "2026-08-05T23:10:00Z")
    # Session du soir du 6 aout : 00h10 UTC le 7, soit 02h10 a Paris.
    _match(migrated, "Zandschulp", "Medvedev", "2026-08-06T00:10:00Z")

    # Les deux jouent la session du soir du 7 aout, soit 00h10 UTC le 8.
    repos = {
        nom: tennis_load.load_for(nom, _competition(migrated), "2026-08-08T00:10:00Z", migrated)
        for nom in ("Paul", "Zandschulp")
    }

    # Les deux ont joue la meme session du soir, a deux journees de tournoi de
    # celle-ci. En dates civiles, l'un donnait 3 jours et l'autre 2.
    assert repos["Paul"].days_rest == 2
    assert repos["Zandschulp"].days_rest == 2


def test_le_parcours_dit_depuis_quand_il_voit(migrated: Settings) -> None:
    """La liste se lisait comme un parcours complet, et elle ne l'est pas : un
    tournoi commence avant notre fenetre de scan a des premiers tours que nous
    n'avons jamais vus.

    Constate en reel — le « Parcours » de Norrie omettait son premier tour contre
    Ugo Carabelli, joue la veille du premier jour scanne, et seule une recherche
    exterieure l'a rattrape. La date rend le trou visible : comparee a « Tour »,
    elle dit tout de suite si le debut du tableau manque.
    """
    _match(migrated, "Norrie", "Buse", "2026-08-05T18:00:00Z")
    _match(migrated, "Norrie", "de Minaur", "2026-08-06T18:00:00Z")

    lignes = dict(
        tennis_load.path_lines(
            "Norrie", "Fils", _competition(migrated), "2026-08-08T23:10:00Z", None, migrated
        )
    )

    assert lignes["Parcours"].endswith("[vu depuis le 05/08]")


def test_les_journees_des_apparitions_sont_gardees(migrated: Settings) -> None:
    """Elles servent la ligne « Fraicheur », qui compte les matchs du tournoi que
    l'historique ne connait pas encore. Des **journees de tournoi** et non des
    dates civiles, pour la meme raison que le repos : a Montreal, un match de la
    session du soir part apres minuit a Paris."""
    _match(migrated, "Fils", "Svajda", "2026-08-05T18:00:00Z")
    _match(migrated, "Fils", "Navone", "2026-08-07T18:00:00Z")

    charge = tennis_load.load_for("Fils", _competition(migrated), "2026-08-09T18:00:00Z", migrated)

    assert charge.days == ("2026-08-05", "2026-08-07")


def test_les_matchs_joues_depuis_la_collecte_se_comptent(migrated: Settings) -> None:
    """Le compte de la ligne « Fraicheur » : trois matchs de ce tournoi joues
    apres la derniere date connue de l'historique sont trois matchs absents de
    « Forme », « Usure », « Profil », « Marge » et « Niveau adv. »."""
    for jour in ("04", "06", "08"):
        _match(migrated, "Fils", f"Adversaire {jour}", f"2026-08-{jour}T18:00:00Z")

    depuis = tennis_load.played_since(
        "Fils", _competition(migrated), "2026-08-09T18:00:00Z", date(2026, 8, 3), migrated
    )

    assert depuis.count == 3
    # **Et il les nomme** : c'est la seule chose que le bloc ne dit nulle part
    # ailleurs. Le compte est sur « Fraicheur », la liste complete sur
    # « Parcours » ; savoir lesquels manquent demandait de croiser les deux.
    assert depuis.opponents == ("Adversaire 04", "Adversaire 06", "Adversaire 08")
    assert depuis.whole_path, "aucun match du parcours n'est connu de l'historique"


def test_un_match_anterieur_a_la_collecte_ne_compte_pas(migrated: Settings) -> None:
    """L'historique le connait deja : le compter ferait douter d'une ligne juste."""
    _match(migrated, "Fils", "Svajda", "2026-08-04T18:00:00Z")
    _match(migrated, "Fils", "Navone", "2026-08-06T18:00:00Z")

    depuis = tennis_load.played_since(
        "Fils", _competition(migrated), "2026-08-08T18:00:00Z", date(2026, 8, 5), migrated
    )

    assert depuis.count == 1
    assert depuis.opponents == ("Navone",), "seul le match posterieur a la collecte"
    assert not depuis.whole_path, "l'autre est deja dans l'historique — Parcours en dit plus"


def test_un_adversaire_est_rapproche_de_sa_propre_journee(migrated: Settings) -> None:
    """`opponents` et `days` sont tries chacun de son cote et ne se remettent pas
    en face l'un de l'autre : sans la paire, nommer « les matchs posterieurs a la
    collecte » attribuerait le mauvais adversaire des qu'un tri differe.

    Les noms sont volontairement dans l'ordre inverse des dates."""
    _match(migrated, "Fils", "Zzz", "2026-08-04T18:00:00Z")
    _match(migrated, "Aaa", "Fils", "2026-08-08T18:00:00Z")

    charge = tennis_load.load_for("Fils", _competition(migrated), "2026-08-09T18:00:00Z", migrated)

    assert charge.faced == (("2026-08-04", "Zzz"), ("2026-08-08", "Aaa"))


# -- Une rencontre programmee n'est pas une rencontre disputee ----------------


def _marquer(settings: Settings, home: str, away: str, outcome: str) -> int:
    row = db.query_one(
        "SELECT id FROM events WHERE home = ? AND away = ?", (home, away), settings=settings
    )
    tennis_load.mark_unplayed(int(row["id"]), outcome, settings)
    return int(row["id"])


def test_un_forfait_ne_compte_pas_dans_le_repos(migrated: Settings) -> None:
    """Bencic s'est retiree trente minutes avant le quart : Gauff est passee sans
    entrer sur le court. Le bloc a servi « Coco Gauff 1j » a une joueuse dont le
    dernier match remontait a quatre journees de tournoi — sur la ligne meme qui
    existe pour dire sa fraicheur, et le fait le plus decisif du lot etait ainsi
    efface.

    Les deux moities sont verifiees ensemble : sans le marquage, le defaut
    d'origine se reproduit a l'identique. Un test qui n'assurerait que la valeur
    corrigee ne dirait pas qu'elle corrige quelque chose.
    """
    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")
    _match(migrated, "Belinda Bencic", "Coco Gauff", "2026-08-11T23:00:00Z")
    quand = "2026-08-13T00:30:00Z"
    args = ("Coco Gauff", "Elena Rybakina", _competition(migrated), quand)

    avant = tennis_load.lines(*args, migrated)
    _marquer(migrated, "Belinda Bencic", "Coco Gauff", tennis_load.WALKOVER)
    apres = tennis_load.lines(*args, migrated)

    assert "Coco Gauff 25 h" in avant[0][1], "le defaut, tant que rien ne dit le forfait"
    assert apres[0][1].startswith("Coco Gauff 3 j 6 h")
    assert "4 j. tournoi" in apres[0][1], "les journees de tournoi restent, en parallele"


def test_un_forfait_sort_du_parcours_et_se_dit_a_part(migrated: Settings) -> None:
    """Le retirer sans un mot ferait chercher un tour manquant : une absence
    constatee est une information, et celle-ci porte sa date et sa cause."""
    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")
    _match(migrated, "Belinda Bencic", "Coco Gauff", "2026-08-11T23:00:00Z")
    _marquer(migrated, "Belinda Bencic", "Coco Gauff", tennis_load.WALKOVER)
    quand = "2026-08-13T00:30:00Z"

    parcours = tennis_load.path_lines(
        "Coco Gauff", "Elena Rybakina", _competition(migrated), quand, None, migrated
    )
    non_joue = tennis_load.unplayed_lines(
        "Coco Gauff", "Elena Rybakina", _competition(migrated), quand, None, migrated
    )

    assert "Belinda Bencic" not in parcours[0][1]
    assert "Alina Korneeva" in parcours[0][1]
    # La date est celle de la **journee de tournoi**, comme partout dans ce
    # module : c'est l'echelle sur laquelle `Repos`, `Fraicheur` et le fichier de
    # resultats se comparent. En donner une autre ici ferait deux calendriers
    # dans le meme bloc.
    assert non_joue == [
        (
            "Non joue",
            "Coco Gauff — Belinda Bencic le 11/08 23:00 UTC, forfait adverse, non disputee",
        )
    ]


def test_un_adversaire_remplace_ne_compte_qu_une_fois(migrated: Settings) -> None:
    """Un joueur ne dispute qu'une rencontre par journee de tournoi. JJ Wolf
    etait programme contre Toby Samuel puis, celui-ci declarant forfait, contre
    Shintaro Mochizuki : le `Parcours` listait les deux, ce qui lui donnait deux
    tours au lieu d'un. **C'est la plus recemment creee qui tient** — aucune
    saisie n'est necessaire, la regle se derive de nos propres scans."""
    _match(migrated, "JJ Wolf", "Toby Samuel", "2026-08-11T19:00:00Z")
    _match(migrated, "JJ Wolf", "Shintaro Mochizuki", "2026-08-11T21:45:00Z")
    quand = "2026-08-12T18:30:00Z"

    charge = tennis_load.load_for("JJ Wolf", _competition(migrated), quand, migrated)
    non_joue = tennis_load.unplayed_lines(
        "JJ Wolf", "Sho Shimabukuro", _competition(migrated), quand, None, migrated
    )

    assert charge.opponents == ("Shintaro Mochizuki",)
    assert "Toby Samuel" in non_joue[0][1]
    assert "adversaire remplace" in non_joue[0][1]


def test_une_rencontre_disputee_ne_produit_aucune_ligne(migrated: Settings) -> None:
    """La ligne est faite pour l'exception. Sur un parcours ordinaire elle ne
    doit rien couter — ni au bloc, ni a la densite."""
    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")

    assert (
        tennis_load.unplayed_lines(
            "Coco Gauff",
            "Elena Rybakina",
            _competition(migrated),
            "2026-08-13T00:30:00Z",
            None,
            migrated,
        )
        == []
    )


def test_un_marquage_se_defait(migrated: Settings) -> None:
    """Se tromper doit pouvoir se defaire, sinon on hesite a marquer et la ligne
    ne sert plus a rien."""
    _match(migrated, "Belinda Bencic", "Coco Gauff", "2026-08-11T23:00:00Z")
    event_id = _marquer(migrated, "Belinda Bencic", "Coco Gauff", tennis_load.WALKOVER)

    tennis_load.mark_unplayed(event_id, "", migrated)

    charge = tennis_load.load_for(
        "Coco Gauff", _competition(migrated), "2026-08-13T00:30:00Z", migrated
    )
    assert charge.opponents == ("Belinda Bencic",)
    assert charge.uncontested == ()


def test_un_etat_hors_vocabulaire_est_refuse(migrated: Settings) -> None:
    """`load_for` ignorerait une valeur inconnue : le marquage paraitrait pose
    et n'aurait aucun effet — le silence exact qu'on corrige."""
    _match(migrated, "Belinda Bencic", "Coco Gauff", "2026-08-11T23:00:00Z")
    row = db.query_one("SELECT id FROM events WHERE home = 'Belinda Bencic'", settings=migrated)

    try:
        tennis_load.mark_unplayed(int(row["id"]), "annule", migrated)
    except ValueError as exc:
        assert "annule" in str(exc)
    else:  # pragma: no cover - le test echoue avant d'y arriver
        raise AssertionError("un etat inconnu doit etre refuse")


def test_un_forfait_ne_compte_pas_dans_les_matchs_non_recenses(migrated: Settings) -> None:
    """« Fraicheur » compte les matchs du tournoi que l'historique ne connait pas
    encore, pour dire ce que `Forme` et `Usure` ignorent. Un match jamais dispute
    n'entrera dans aucune de ces lignes : le compter enverrait chercher un score
    qui n'existe pas."""
    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")
    _match(migrated, "Belinda Bencic", "Coco Gauff", "2026-08-11T23:00:00Z")
    _marquer(migrated, "Belinda Bencic", "Coco Gauff", tennis_load.WALKOVER)

    manquants = tennis_load.played_since(
        "Coco Gauff", _competition(migrated), "2026-08-13T00:30:00Z", date(2026, 8, 3), migrated
    )

    assert manquants.count == 1
    assert manquants.opponents == ("Alina Korneeva",)


def _session(settings: Settings, home: str, away: str, when: str) -> int:
    """Une session portant ce match, pour eprouver le prompt reellement rendu."""
    _match(settings, home, away, when)
    row = db.query_one(
        "SELECT id FROM events WHERE home = ? AND away = ?", (home, away), settings=settings
    )
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (1, ?)",
        (row["id"],),
        settings=settings,
    )
    return 1


def test_le_mode_d_emploi_de_non_joue_ne_se_paie_que_sur_un_lot_qui_en_porte(
    migrated: Settings,
) -> None:
    """Meme regle que partout : le preambule ne documente que les lignes que le
    lot porte vraiment. Un forfait est l'exception, son explication ne doit pas
    peser sur les sessions ordinaires."""
    from myassistantbet.services.prompt import build_prompt

    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")
    _match(migrated, "Belinda Bencic", "Coco Gauff", "2026-08-11T23:00:00Z")
    session_id = _session(migrated, "Coco Gauff", "Elena Rybakina", "2026-08-13T00:30:00Z")

    sain = " ".join(build_prompt(session_id, settings=migrated).body.split())
    _marquer(migrated, "Belinda Bencic", "Coco Gauff", tennis_load.WALKOVER)
    forfait = " ".join(build_prompt(session_id, settings=migrated).body.split())

    assert "**« Non joué »** liste les rencontres programmées" not in sain
    assert "**« Non joué »** liste les rencontres programmées" in forfait
    assert "forfait adverse, non disputee" in forfait, (
        "le bloc porte la ligne, pas seulement l'aide"
    )


def test_le_preambule_ne_promet_plus_qu_un_forfait_se_lit_comme_un_match_joue(
    migrated: Settings,
) -> None:
    """La phrase etait vraie tant que rien ne detectait un forfait ; elle est
    devenue fausse le jour ou « Non joue » existe. Toute condition ajoutee a une
    ligne se verifie contre la phrase du preambule qui explique son absence —
    corriger un rendu sans relire son mode d'emploi laisse une affirmation fausse
    a l'endroit precis ou l'on vient de gagner en justesse."""
    from myassistantbet.services.prompt import build_prompt

    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")
    session_id = _session(migrated, "Coco Gauff", "Elena Rybakina", "2026-08-13T00:30:00Z")

    corps = " ".join(build_prompt(session_id, settings=migrated).body.split())

    assert "si bien qu'un forfait s'y lit comme un match joué" not in corps
    assert "L'absence de « Non joué » ne prouve donc pas" in corps


# -- Le repos en temps reel ecoule -------------------------------------------


def _fuseau(settings: Settings, name: str) -> None:
    db.execute(
        "UPDATE competitions SET timezone = ? WHERE id = ?",
        (name, _competition(settings)),
        settings=settings,
    )


def test_deux_sessions_du_meme_jour_ne_donnent_plus_deux_repos_differents(
    migrated: Settings,
) -> None:
    """Le defaut mesure sur les demi-finales du Canadian Open : le bloc donnait
    2j aux joueurs sortis de la session de jour et 1j a ceux de la session du
    soir, quand l'ecart reel etait d'environ vingt-quatre heures pour tout le
    monde. La journee de tournoi ne sait pas dire cette grandeur-la."""
    # Quarts du 11 aout a Montreal : session de jour a 17h00 UTC, session du soir
    # a 23h50 — soit apres minuit a Paris, donc une autre journee de tournoi.
    _match(migrated, "Jour", "A", "2026-08-11T17:00:00Z")
    _match(migrated, "Soir", "B", "2026-08-11T23:50:00Z")

    # Les deux jouent **la meme** demi-finale, le 12 aout a 23h00 UTC.
    charges = {
        nom: tennis_load.load_for(nom, _competition(migrated), "2026-08-12T23:00:00Z", migrated)
        for nom in ("Jour", "Soir")
    }

    # La journee de tournoi les separe d'une journee entiere...
    assert (charges["Jour"].days_rest, charges["Soir"].days_rest) == (2, 1)
    # ...quand sept heures les separent vraiment.
    assert (charges["Jour"].rest.hours, charges["Soir"].rest.hours) == (30, 23)


def test_une_bascule_de_date_a_paris_ne_fait_plus_un_repos_nul(migrated: Settings) -> None:
    """Six joueuses de Cincinnati recevaient « Repos 0j » : leur premier tour
    s'etait joue la veille en fin d'apres-midi local, mais 23h00 UTC tombe apres
    minuit a Paris, si bien que les deux matchs partageaient une date."""
    _match(migrated, "Mertens", "A", "2026-08-11T23:00:00Z")

    charge = tennis_load.load_for(
        "Mertens", _competition(migrated), "2026-08-12T21:00:00Z", migrated
    )

    assert charge.days_rest == 0, "l'ancien defaut, tel quel"
    assert charge.rest.hours == 22


def test_le_repos_dit_sur_quoi_il_a_ete_mesure(migrated: Settings) -> None:
    """Le chiffre seul ne suffit pas : deux joueurs a « 23 h » et « 26 h » ne se
    comparent que si l'on sait lequel est releve et lequel est deduit. Aucune
    source ne publie la duree d'un match — le calcul part donc du coup d'envoi,
    et la ligne le dit plutot que de laisser croire a une fin de match."""
    _match(migrated, "Fils", "A", "2026-08-05T18:00:00Z")

    ligne = tennis_load.lines(
        "Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )[0][1]

    assert "depuis 05/08 18:00" in ligne, "l'instant de reference est le coup d'envoi"
    assert "~" not in ligne, "rien n'est estime aujourd'hui, donc rien ne doit le laisser croire"


def test_le_fuseau_du_lieu_date_le_fait_la_ou_il_se_produit(migrated: Settings) -> None:
    """Une heure de Paris presentee comme locale serait pire qu'une heure UTC
    presentee comme distante. Non renseigne, rien n'est invente."""
    _match(migrated, "Fils", "A", "2026-08-05T23:00:00Z")
    args = ("Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z")

    sans = tennis_load.lines(*args, migrated)[0][1]
    _fuseau(migrated, "America/Toronto")
    avec = tennis_load.lines(*args, migrated)[0][1]

    assert "depuis 05/08 23:00 UTC" in sans
    assert "depuis 05/08 19:00 local" in avec, "19h00 a Toronto, et le 5, pas le 6"


def test_un_fuseau_illisible_vaut_non_renseigne(migrated: Settings) -> None:
    """Une saisie devenue illisible d'une version de tzdata a l'autre ne doit pas
    faire tomber le bloc entier pour une colonne d'appoint."""
    _match(migrated, "Fils", "A", "2026-08-05T18:00:00Z")
    _fuseau(migrated, "Mars/Olympus_Mons")

    ligne = tennis_load.lines(
        "Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )[0][1]

    assert "UTC" in ligne


def test_au_dela_de_trois_jours_l_ecart_se_lit_en_jours(migrated: Settings) -> None:
    """« 121 h » demande une division, « 5 j » se lit d'un coup. En dessous c'est
    l'inverse — c'est justement la que la journee de tournoi confondait 24 h et
    0 h. Les heures restent ecrites : c'est la grandeur mesuree."""
    _match(migrated, "Fils", "A", "2026-08-01T18:00:00Z")

    ligne = tennis_load.lines(
        "Fils", "Autre", _competition(migrated), "2026-08-06T18:00:00Z", migrated
    )[0][1]

    assert ligne.startswith("Fils 5 j 0 h")


def test_le_mode_d_emploi_du_repos_dit_ce_qu_il_ne_faut_pas_en_faire(migrated: Settings) -> None:
    """La mention compte autant que le chiffre : sans elle, deux joueurs a
    « 23 h » et « 30 h » se comparent comme si les deux etaient releves, alors
    que les deux partent d'un coup d'envoi et ignorent la duree des matchs."""
    from myassistantbet.services.prompt import build_prompt

    _match(migrated, "Alina Korneeva", "Coco Gauff", "2026-08-09T18:00:00Z")
    session_id = _session(migrated, "Coco Gauff", "Elena Rybakina", "2026-08-13T00:30:00Z")

    corps = " ".join(build_prompt(session_id, settings=migrated).body.split())

    assert "le temps **réellement écoulé** depuis le dernier match" in corps
    assert "Ne compare donc pas deux écarts à l'heure près**" in corps
    assert "en parallèle et non à la place" in corps


def test_la_fraicheur_dit_les_bords_de_la_fenetre_de_scan(migrated: Settings) -> None:
    """« vu depuis le 04/08 » etait ambigu : premier jour du tournoi, ou premier
    jour ou nous avons regarde ? La borne haute compte autant — vieille de deux
    jours, elle dit qu'un scan ne tourne plus, et rien d'autre ne le dirait."""
    _match(migrated, "Fils", "A", "2026-08-05T18:00:00Z")

    fenetre = tennis_load.scan_window(_competition(migrated), migrated)

    assert fenetre.startswith("scans du ")
    assert fenetre.endswith(" UTC")


def test_sans_evenement_la_fenetre_de_scan_ne_dit_rien(migrated: Settings) -> None:
    """Une fenetre inventee sur une competition jamais scannee ferait chercher un
    trou de collecte la ou il n'y a rien eu a collecter."""
    assert tennis_load.scan_window(_competition(migrated), migrated) == ""
