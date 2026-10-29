"""The package writes exactly the notifications today's producers write.

tests/golden/esperado.json is what analyse, the citation and boletín scrapers
and live inserted for tests/casos.py (scripts/grabar_golden.py, from each
repo's origin/main). Every row must match column for column, in the same
order, message included.

Two differences are intended and have their own tests below:
- boletín alerts are unified: mentions go to every follower (the recording
  already feeds every follower to the old code as if all had alert_on_mentions);
- live folded accents with NFKD; the package uses NFD like the other producers.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alertas import (Citacion, Movimiento, Palabra, Seguimiento, Sesion, Turno, alertas_citacion,
                     alertas_movimiento, alertas_sesion, alertas_turno, coincide)
from tests import casos

ESPERADO = json.loads((Path(__file__).parent / "golden" / "esperado.json").read_text(encoding="utf-8"))
PALABRAS = [Palabra(*p) for p in casos.PALABRAS]
SEGUIDORES = [Seguimiento(*s) for s in casos.SEGUIDORES]


def _filas(alertas):
    return [a.fila for a in alertas]


def test_sesiones():
    for s in casos.sesiones():
        obtenido = _filas(alertas_sesion(Sesion(**s), PALABRAS, SEGUIDORES, casos.TITULOS))
        assert obtenido == ESPERADO["sesiones"][str(s["id"])], s["id"]


def test_citaciones():
    for c in casos.citaciones():
        obtenido = _filas(alertas_citacion(Citacion(**c), PALABRAS, SEGUIDORES, casos.TITULOS, casos.HOY))
        assert obtenido == ESPERADO["citaciones"][str(c["id"])], c["id"]


def test_movimientos():
    for (boletin, antes, despues), esperado in zip(casos.MOVIMIENTOS, ESPERADO["movimientos"]):
        obtenido = _filas(alertas_movimiento(Movimiento(boletin, antes, despues), SEGUIDORES,
                                             casos.TITULOS.get(boletin)))
        assert obtenido == esperado, boletin


def test_turnos():
    """Live kept a per-plenary in-memory set; here (user, regla, objetivo) is the
    alertas_enviadas key, so a repeat in the same window is dropped the same way."""
    enviadas = set()
    for (camara, plenario, texto, inicio), esperado in zip(casos.turnos(), ESPERADO["turnos"]):
        nuevas = []
        for a in alertas_turno(Turno(plenario, camara, texto, inicio), PALABRAS):
            if (a.user_id, a.regla, a.objetivo) not in enviadas:
                enviadas.add((a.user_id, a.regla, a.objetivo))
                nuevas.append(a.fila)
        assert nuevas == esperado, (plenario, inicio)


def test_las_claves_no_se_repiten_entre_fuentes():
    claves = set()
    for s in casos.sesiones():
        for a in alertas_sesion(Sesion(**s), PALABRAS, SEGUIDORES, casos.TITULOS):
            assert (a.user_id, a.regla, a.objetivo) not in claves
            claves.add((a.user_id, a.regla, a.objetivo))
    for c in casos.citaciones():
        for a in alertas_citacion(Citacion(**c), PALABRAS, SEGUIDORES, casos.TITULOS, casos.HOY):
            assert (a.user_id, a.regla, a.objetivo) not in claves
            claves.add((a.user_id, a.regla, a.objetivo))


def test_menciones_a_todos_los_seguidores():
    """Unified boletín alerts: user 3 follows 15222-06 and is alerted of its
    mentions, with no alert_on_mentions flag involved."""
    s = next(x for x in casos.sesiones() if x["id"] == 202)
    alertas = alertas_sesion(Sesion(**s), PALABRAS, SEGUIDORES, casos.TITULOS)
    assert {(a.user_id, a.regla) for a in alertas if a.regla.startswith("mencion:")} == \
        {(1, "mencion:15222-06"), (3, "mencion:15222-06")}


def test_un_movimiento_es_una_alerta_por_numero_de_tramites():
    a = alertas_movimiento(Movimiento("15222-06", 3, 4), SEGUIDORES, None)
    b = alertas_movimiento(Movimiento("15222-06", 4, 5), SEGUIDORES, None)
    assert {x.regla for x in a} == {"movimiento:15222-06:4"} and {x.regla for x in b} == {"movimiento:15222-06:5"}


def test_diferencia_aceptada_con_live_nfkd():
    """Live's NFKD turned º into o, so 'no 5' matched 'Nº 5'. Like analyse, the
    scrapers and the webapp, the package keeps º as it is."""
    assert not coincide("no 5", "el Nº 5 de la tabla", "exact")
    assert coincide("nº 5", "el Nº 5 de la tabla", "exact")
