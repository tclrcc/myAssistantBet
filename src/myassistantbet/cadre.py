"""Lire le cadre publie, et enregistrer la preuve de l'avoir lu.

    uv run myassistantbet-cadre              # dit ce que le cadre publie declare
    uv run myassistantbet-cadre --relire     # ecrit la preuve, pour un bump

**Le second geste est ce qui distingue une preuve d'une declaration.** Il lit le
fichier reel, en prend l'empreinte, et ecrit `deploy/cadre-lu.json` — versionne,
donc embarque dans le commit qui bouge `FRAMEWORK_VERSION`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from .services import framework
from .services.prompt import FRAMEWORK_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lit le cadre publie sur cette machine. --relire enregistre la preuve "
            "de lecture qui accompagne un changement de FRAMEWORK_VERSION."
        )
    )
    parser.add_argument(
        "--relire",
        action="store_true",
        help="ecrit deploy/cadre-lu.json a partir du cadre lu a l'instant",
    )
    args = parser.parse_args(argv)

    maintenant = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lu = framework.published(now=maintenant)
    if lu is None:
        print("Aucun cadre publie lisible sur cette machine.", file=sys.stderr)
        garde = framework.recorded()
        if garde is not None:
            print(f"Preuve enregistree : version {garde.version}, lue le {garde.read_at}.")
        # **Un echec, pas un silence.** Sans exemplaire lisible il n'y a rien a
        # prouver, et rendre 0 ferait passer l'absence pour une verification.
        return 1

    print(f"Cadre publie   : {lu.version}")
    print(f"  fichier      : {lu.path}")
    print(f"  empreinte    : {lu.sha256[:16]}…")
    print(f"Constante code : {FRAMEWORK_VERSION}")
    if lu.version != FRAMEWORK_VERSION:
        print(
            "\nÉcart : la constante et le cadre publié ne disent pas la même chose. "
            "Bumper la constante demande --relire ; le cadre, lui, ne se corrige pas d'ici.",
            file=sys.stderr,
        )

    if args.relire:
        chemin = framework.record(lu)
        print(f"\nPreuve écrite : {chemin}")
        print("Commite-la avec le changement de FRAMEWORK_VERSION.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
