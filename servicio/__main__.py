"""python -m servicio backtest --desde 2026-10-20 [--hasta ...] [--sin-comparar]
python -m servicio servir [--simular]
python -m servicio marcar --desde 2026-11-10T15:00 sesiones_senate sesiones_camara ..."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from servicio import db
from servicio.servir import Servicio, marcar, nombres_de_entorno
from servicio.simular import backtest, imprimir


def _fecha(texto: str) -> datetime:
    t = datetime.fromisoformat(texto)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    ap = argparse.ArgumentParser(prog="servicio")
    sub = ap.add_subparsers(dest="orden", required=True)
    b = sub.add_parser("backtest", help="replay a past window and compare with what the producers wrote")
    b.add_argument("--desde", required=True, type=_fecha, help="ISO date or time, UTC if no zone")
    b.add_argument("--hasta", type=_fecha)
    b.add_argument("--sin-comparar", action="store_true")
    s = sub.add_parser("servir", help="the always-on loop")
    s.add_argument("--simular", action="store_true", help="log the alerts instead of writing them")
    s.add_argument("--intervalo", type=float, default=float(os.getenv("ALERTAS_INTERVALO_S", "60")))
    s.add_argument("--intervalo-turnos", type=float, default=float(os.getenv("ALERTAS_INTERVALO_TURNOS_S", "5")))
    m = sub.add_parser("marcar", help="start sources at their first item at or after --desde (a cutover)")
    m.add_argument("--desde", required=True, type=_fecha, help="when the producer stopped alerting, UTC if no zone")
    m.add_argument("fuentes", nargs="+")
    a = ap.parse_args(argv)

    if a.orden == "marcar":
        nombres = [n for f in a.fuentes for n in f.split(",") if n]
        marcar(db.contenido(solo_lectura=True), db.sitio(), nombres, a.desde)
        return 0
    if a.orden == "backtest":
        imprimir(backtest(db.contenido(solo_lectura=True), db.sitio(solo_lectura=True), a.desde, a.hasta,
                          con_comparacion=not a.sin_comparar))
        return 0
    simular = a.simular or os.getenv("ALERTAS_SIMULAR", "").lower() in ("1", "true", "si", "sí")
    Servicio(simular, nombres_de_entorno(), a.intervalo, a.intervalo_turnos).correr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
