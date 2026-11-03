"""The service against a real Postgres (ALERTAS_TEST_DSN, e.g. a local container).

The golden cases are loaded as production-shaped tables, with the notifications
the producers wrote for them (tests/golden/esperado.json) as history. A backtest
over them must find every notification again with the same message; the writer
must insert each alert once however often it runs; the dry run must write nothing.
"""
import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alertas import Sesion, alertas_sesion
from servicio.servir import Servicio
from servicio.simular import _solo_nuevo, backtest
from servicio.sitio import leer_listas
from tests import casos

DSN = os.getenv("ALERTAS_TEST_DSN")
RAIZ = Path(__file__).resolve().parent.parent
ESPERADO = json.loads((RAIZ / "tests" / "golden" / "esperado.json").read_text(encoding="utf-8"))
VENTANA = "2026-10-29 12:00:00+00"

pytestmark = pytest.mark.skipif(not DSN, reason="ALERTAS_TEST_DSN no definido")


def _conn():
    import psycopg2
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def _cargar(cur):
    cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    cur.execute((RAIZ / "tests" / "esquema_prueba.sql").read_text())
    cur.execute((RAIZ / "sql" / "alertas_sitio.sql").read_text())
    for id_, user, kw, mt, tipo in casos.PALABRAS:
        cur.execute("INSERT INTO bicameral_keywords VALUES (%s,%s,%s,%s,%s,TRUE,'2026-01-01')", (id_, user, kw, tipo, mt))
    for user, bol in casos.SEGUIDORES:
        cur.execute("INSERT INTO user_tracked_boletines VALUES (%s,%s,TRUE)", (user, bol))
    for bol, titulo in casos.TITULOS.items():
        cur.execute("INSERT INTO boletines VALUES (%s,%s)", (bol, titulo))
    for s in casos.sesiones():
        hora = s["time"] or None
        if s["camara"] == "senate":
            # the long commission name lives on the citation, created long before the window
            cur.execute("INSERT INTO senate_citations (id, commission, commission_original, created_at) "
                        "VALUES (%s,%s,%s,'2026-01-01')", (s["id"] - 100, s["commission"], s["commission_original"]))
            cur.execute("INSERT INTO senate_sessions (id, citation_id, commission, date, time, title, materia, boletin, "
                        "transcript_text, analysis_text, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (s["id"], s["id"] - 100, s["commission"], s["date"], hora, s["title"], s["materia"],
                         s["boletin"], s["transcript_text"], s["analysis_text"], VENTANA))
        else:
            cur.execute("INSERT INTO camara_sessions (id, commission, commission_original, date, time, title, materia, "
                        "boletin, transcript_text, analysis_text, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (s["id"], s["commission"], s["commission_original"], s["date"], hora, s["title"], s["materia"],
                         s["boletin"], s["transcript_text"], s["analysis_text"], VENTANA))
    for c in casos.citaciones():
        tabla = "senate_citations" if c["camara"] == "senate" else "camara_citations"
        cur.execute(f"INSERT INTO {tabla} (id, commission, commission_original, date, time, materia, boletin, created_at) "
                    f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (c["id"], c["commission"], c["commission_original"], c["date"],
                                                         c["time"] or None, c["materia"], c["boletin"], VENTANA))
    for i, (camara, plenario, texto, inicio) in enumerate(casos.turnos()):
        cur.execute("INSERT INTO plenary_turns (chamber, plenary_id, turn_index, start_seconds, end_seconds, text, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)", (camara, plenario, i, inicio, inicio + 1, texto, VENTANA))
    historia = [f for filas in ESPERADO["sesiones"].values() for f in filas] + \
        [f for filas in ESPERADO["citaciones"].values() for f in filas] + [f for filas in ESPERADO["turnos"] for f in filas]
    for f in historia:
        cols = list(f)
        cur.execute(f"INSERT INTO bicameral_notifications ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    [f[c] for c in cols])
    return len(historia)


@pytest.fixture()
def base():
    c = _conn()
    with c.cursor() as cur:
        n = _cargar(cur)
    yield c, n
    c.close()


def test_el_backtest_reencuentra_cada_notificacion(base):
    _, n = base
    informe = backtest(_ro(), _ro(), _desde(), None)
    assert informe["notificaciones_antes"] == n
    assert informe["alertas_ahora"] == n
    for tipo, categorias in informe["conteo"].items():
        assert set(categorias) == {"igual"}, (tipo, categorias, informe["ejemplos"])
    assert informe["items"]["sesiones_senate"] == 3 and informe["items"]["citaciones_camara"] == 3


def _ro():
    c = _conn()
    with c.cursor() as cur:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return c


def _desde():
    from datetime import datetime, timezone
    return datetime(2026, 10, 29, tzinfo=timezone.utc)


def test_una_palabra_creada_despues_no_cuenta_en_el_backtest(base):
    c, _ = base
    with c.cursor() as cur:
        cur.execute("UPDATE bicameral_keywords SET created_at = '2026-10-30' WHERE id = 13")
    informe = backtest(_ro(), _ro(), _desde(), None)
    # the old notifications of keyword 13 now have no new counterpart; the rest still match
    assert informe["conteo"]["palabra"].get("sin_explicar", 0) > 0
    assert all(e["clave"][2] == 13 for e in informe["ejemplos"]["sin_explicar"])


def test_el_escritor_inserta_cada_alerta_una_vez(base):
    c, _ = base
    serv = Servicio(False, ["sesiones_camara"], 60, 5, contenido=_conn(), sitio=_conn())
    serv.preparar()
    s = next(x for x in casos.sesiones() if x["id"] == 202)
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        antes = cur.fetchone()[0]
        cur.execute("INSERT INTO camara_sessions (id, commission, commission_original, date, time, title, materia, "
                    "analysis_text) VALUES (300,%s,%s,%s,%s,%s,%s,%s)",
                    (s["commission"], s["commission_original"], s["date"], s["time"], s["title"], s["materia"],
                     s["analysis_text"]))
    with c.cursor() as cur:
        listas = leer_listas(cur, cur)
    esperadas = alertas_sesion(Sesion(**dict(s, id=300)), listas.palabras, listas.seguidores, listas.titulos)
    assert serv.una_pasada(ahora=1e9) == len(esperadas) > 0
    serv._proxima.clear()
    assert serv.una_pasada(ahora=2e9) == 0
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        assert cur.fetchone()[0] == antes + len(esperadas)
        cur.execute("SELECT marca FROM alertas_estado WHERE fuente = 'sesiones_camara'")
        assert cur.fetchone()[0] == 300
        # a user deleting the notification does not bring it back
        cur.execute("DELETE FROM bicameral_notifications WHERE target_id = 300")
    serv.marcas["sesiones_camara"] = 299
    serv._proxima.clear()
    assert serv.una_pasada(ahora=3e9) == 0

    otra = Servicio(False, ["sesiones_camara"], 60, 5, contenido=_conn(), sitio=_conn())
    with pytest.raises(RuntimeError, match="cerrojo"):
        otra.preparar()


def test_el_simulacro_no_escribe_nada(base, caplog):
    c, _ = base
    serv = Servicio(True, ["turnos", "citaciones_camara"], 60, 5, contenido=_ro(), sitio=_ro())
    serv.preparar()
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        antes = cur.fetchone()[0]
        cur.execute("INSERT INTO plenary_turns (chamber, plenary_id, turn_index, start_seconds, text) "
                    "VALUES ('senate', 88, 0, 1.0, 'La reforma y el Ministro del Interior')")
    with caplog.at_level(logging.INFO):
        # 'reforma' (keyword 1) and 'Ministro del Interior' (keyword 3) apply to plenaries
        assert serv.una_pasada(ahora=1e9) == 2
    assert sum("ALERTA_SIMULADA" in r.getMessage() for r in caplog.records) == 2
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        assert cur.fetchone()[0] == antes
        cur.execute("SELECT COUNT(*) FROM alertas_estado")
        assert cur.fetchone()[0] == 0


def test_motivos_de_las_alertas_nuevas():
    m = {"palabras": set(), "seguidos": {(1, "15222-06"): False}, "cit_kw": {(4, 4, 305)}, "cit_bol": set()}
    assert _solo_nuevo(("mencion", 1, "15222-06", "session", "camara", 202), m) == "menciones_unificadas"
    assert _solo_nuevo(("palabra", 4, 4, "citation", "camara", 305), m) == "borrada_por_usuario"
    assert _solo_nuevo(("palabra", 4, 4, "session", "camara", 305), m) == "sin_explicar"
