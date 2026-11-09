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
    cur.execute("DROP SCHEMA IF EXISTS jurispeed CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    cur.execute((RAIZ / "tests" / "esquema_prueba.sql").read_text())
    cur.execute((RAIZ / "sql" / "alertas_sitio.sql").read_text())
    cur.execute((RAIZ / "sql" / "citaciones_editadas.sql").read_text())
    cur.execute((RAIZ / "sql" / "boletin_movimientos.sql").read_text())
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


# -- step 2: citation cutover ------------------------------------------------------

CITAS = ["citaciones_camara", "citaciones_camara_editadas"]


def _hace(minutos):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(minutes=minutos)


def _citacion_nueva(cur, id_, materia):
    """A Cámara citation for today that matches no keyword unless `materia` does."""
    cur.execute("INSERT INTO camara_citations (id, commission, commission_original, date, time, materia, source_url) "
                "VALUES (%s, 'hacienda', 'Comisión de Hacienda', CURRENT_DATE, '15:00', %s, 'u1')", (id_, materia))


def _escritor():
    serv = Servicio(False, CITAS, 60, 5, contenido=_conn(), sitio=_conn())
    serv.preparar()
    return serv


def test_el_disparador_marca_solo_las_ediciones_que_cuentan(base):
    c, _ = base
    with c.cursor() as cur:
        _citacion_nueva(cur, 400, "Tema neutro")
        cur.execute("SELECT updated_at FROM camara_citations WHERE id = 400")
        assert cur.fetchone()[0] is None  # an insert is not an edit
        cur.execute("UPDATE camara_citations SET materia = 'Tema neutro', time = '16:00' WHERE id = 400")
        cur.execute("SELECT updated_at FROM camara_citations WHERE id = 400")
        assert cur.fetchone()[0] is None  # same materia; time is not one of the four
        cur.execute("UPDATE camara_citations SET source_url = 'u2' WHERE id = 400")
        cur.execute("SELECT updated_at FROM camara_citations WHERE id = 400")
        assert cur.fetchone()[0] is not None


def test_una_citacion_editada_alerta_lo_nuevo_y_queda_en_el_registro_antiguo(base):
    from servicio.fuentes import _us
    c, _ = base
    with c.cursor() as cur:
        _citacion_nueva(cur, 400, "Tema neutro")
    serv = _escritor()
    assert serv.registro_antiguo
    serv.marcas["citaciones_camara"] = 399
    serv.marcas["citaciones_camara_editadas"] = _us(_hace(10))
    assert serv.una_pasada(ahora=1e9) == 0
    with c.cursor() as cur:
        # the scraper rewrites the materia; 'subrogación' is keyword 5 (user 1, citations)
        cur.execute("UPDATE camara_citations SET materia = 'Proyecto sobre subrogación de cargos' WHERE id = 400")
        cur.execute("UPDATE camara_citations SET updated_at = %s WHERE id = 400", (_hace(2),))
    serv._proxima.clear()
    assert serv.una_pasada(ahora=2e9) == 1
    with c.cursor() as cur:
        cur.execute("SELECT user_id, keyword_id, message FROM bicameral_notifications WHERE target_id = 400")
        user_id, keyword_id, mensaje = cur.fetchone()
        assert (user_id, keyword_id) == (1, 5) and "**subrogación**" in mensaje
        cur.execute("SELECT citation_date = CURRENT_DATE FROM citation_keyword_notification_sent "
                    "WHERE user_id = 1 AND keyword_id = 5 AND citation_id = 400")
        assert cur.fetchone()[0]
    # edited again with the same keyword: nothing new
    with c.cursor() as cur:
        cur.execute("UPDATE camara_citations SET materia = 'Subrogación, segunda versión', updated_at = %s "
                    "WHERE id = 400", (_hace(2),))
        cur.execute("UPDATE camara_citations SET updated_at = %s WHERE id = 400", (_hace(2),))
    serv.marcas["citaciones_camara_editadas"] = _us(_hace(10))
    serv._proxima.clear()
    assert serv.una_pasada(ahora=3e9) == 0


def test_lo_que_ya_envio_el_scraper_no_se_repite(base):
    c, _ = base
    with c.cursor() as cur:
        _citacion_nueva(cur, 401, "Proyecto sobre subrogación de cargos")
        cur.execute("INSERT INTO citation_keyword_notification_sent VALUES (1, 5, 401, CURRENT_DATE)")
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        antes = cur.fetchone()[0]
    serv = _escritor()
    serv.marcas["citaciones_camara"] = 400
    assert serv.una_pasada(ahora=1e9) == 0
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        assert cur.fetchone()[0] == antes
        cur.execute("SELECT notification_id FROM alertas_enviadas WHERE regla = 'keyword:5' "
                    "AND objetivo = 'citation:camara:401'")
        assert cur.fetchone() == (None,)  # remembered as sent, by the scraper


def test_cada_pasada_deja_el_latido_aunque_no_haya_nada(base):
    c, _ = base
    serv = _escritor()
    with c.cursor() as cur:
        cur.execute("UPDATE alertas_estado SET actualizado_en = NOW() - INTERVAL '2 hours'")
    serv.una_pasada(ahora=1e9)
    with c.cursor() as cur:
        cur.execute("SELECT fuente FROM alertas_estado WHERE actualizado_en > NOW() - INTERVAL '1 minute' ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == sorted(CITAS)


def test_si_falla_la_notificacion_no_queda_como_enviada(base):
    c, _ = base
    with c.cursor() as cur:
        _citacion_nueva(cur, 402, "Proyecto sobre subrogación de cargos")
        cur.execute("ALTER TABLE bicameral_notifications ADD CONSTRAINT falla CHECK (user_id < 0) NOT VALID")
    serv = _escritor()
    serv.marcas["citaciones_camara"] = 401
    import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation):
        serv.una_pasada(ahora=1e9)
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM alertas_enviadas")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM citation_keyword_notification_sent WHERE citation_id = 402")
        assert cur.fetchone()[0] == 0


# -- every source: boletín movements and cutover marks ------------------------------

def _movimiento(cur, boletin, antes, despues, creado=None):
    cur.execute("INSERT INTO boletin_movimientos (boletin, tramites_antes, tramites_despues, created_at) "
                "VALUES (%s, %s, %s, COALESCE(%s, NOW())) RETURNING id", (boletin, antes, despues, creado))
    return cur.fetchone()[0]


def test_un_movimiento_alerta_a_cada_seguidor_una_vez(base):
    c, _ = base
    serv = Servicio(False, ["movimientos"], 60, 5, contenido=_conn(), sitio=_conn())
    serv.preparar()
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        antes = cur.fetchone()[0]
        _movimiento(cur, "15222-06", 3, 4)   # followed by users 1 and 3
        _movimiento(cur, "17662-07", 0, 2)   # first count seen: baseline only
        _movimiento(cur, "11111-11", 1, 2)   # nobody follows it
    assert serv.una_pasada(ahora=1e9) == 2
    with c.cursor() as cur:
        cur.execute("SELECT user_id, origin, target_boletin, message FROM bicameral_notifications "
                    "WHERE id > 0 ORDER BY id DESC LIMIT 2")
        filas = sorted(cur.fetchall())
        assert [(u, o, b) for u, o, b, _ in filas] == [(1, "boletin_movement", "15222-06"),
                                                       (3, "boletin_movement", "15222-06")]
        assert filas[0][3].startswith("Nuevo movimiento en boletín 15222-06\nProyecto: «Modifica")
        cur.execute("SELECT COUNT(*) FROM bicameral_notifications")
        assert cur.fetchone()[0] == antes + 2
    # the same movement read again (a restart before the mark was kept) sends nothing
    serv.marcas["movimientos"] = 0
    serv._proxima.clear()
    assert serv.una_pasada(ahora=2e9) == 0


def test_marcar_empieza_en_lo_que_el_productor_ya_no_alerto(base):
    from datetime import timedelta
    from servicio.servir import marcar
    c, _ = base
    with c.cursor() as cur:
        viejo = _movimiento(cur, "15222-06", 3, 4, _hace(120))   # the scraper alerted this one
        nuevo = _movimiento(cur, "15222-06", 4, 5, _hace(30))    # after it stopped
    marcas = marcar(_conn(), _conn(), ["movimientos"], _hace(60))
    assert marcas == {"movimientos": viejo}
    serv = Servicio(False, ["movimientos"], 60, 5, contenido=_conn(), sitio=_conn())
    serv.preparar()
    assert serv.marcas["movimientos"] == viejo  # kept, not reset to the newest row
    assert serv.una_pasada(ahora=1e9) == 2      # only the 4 -> 5 movement, for users 1 and 3
    with c.cursor() as cur:
        cur.execute("SELECT DISTINCT regla FROM alertas_enviadas")
        assert [r[0] for r in cur.fetchall()] == ["movimiento:15222-06:5"]
    with pytest.raises(ValueError):
        marcar(_conn(), _conn(), ["no_existe"], _hace(60))


def test_el_backtest_compara_los_movimientos(base):
    c, _ = base
    with c.cursor() as cur:
        _movimiento(cur, "15222-06", 3, 4, VENTANA)
        for user in (1, 3):  # what the scraper wrote for it
            cur.execute("INSERT INTO bicameral_notifications (user_id, type, related_id, chamber, message, boletin, "
                        "origin, target_type, target_boletin, created_at) VALUES (%s, 'boletin', 0, 'senate', 'm', "
                        "'15222-06', 'boletin_movement', 'boletin', '15222-06', %s)", (user, VENTANA))
    informe = backtest(_ro(), _ro(), _desde(), None)
    assert informe["conteo"]["movimiento"] == {"igual": 2}
    assert informe["items"]["boletines_movidos"] == 1


def test_el_backtest_sigue_sin_los_registros_de_los_scrapers(base):
    c, _ = base
    with c.cursor() as cur:
        cur.execute("DROP TABLE citation_keyword_notification_sent, citation_boletin_notification_sent, "
                    "boletin_movimientos")
    informe = backtest(_ro(), _ro(), _desde(), None)
    assert "movimiento" not in informe["conteo"]
    assert set(informe["conteo"]["palabra"]) == {"igual"}



# -- another site sharing the database, in its own schema ---------------------------

ESQUEMA_JURISPEED = """
CREATE TABLE jurispeed.keywords (id INTEGER PRIMARY KEY, user_id INTEGER, keyword VARCHAR, type VARCHAR,
    match_type VARCHAR, email_enabled BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
CREATE TABLE jurispeed.user_tracked_boletines (user_id INTEGER, boletin VARCHAR, alert_on_mentions BOOLEAN,
    PRIMARY KEY (user_id, boletin));
CREATE TABLE jurispeed.notifications (id SERIAL PRIMARY KEY, user_id INTEGER, keyword_id INTEGER,
    type VARCHAR, related_id INTEGER, chamber VARCHAR, message TEXT, boletin VARCHAR, origin VARCHAR,
    target_type VARCHAR, target_id INTEGER, target_chamber VARCHAR, target_boletin VARCHAR,
    created_at TIMESTAMP DEFAULT NOW());
INSERT INTO jurispeed.keywords (id, user_id, keyword, type, match_type) VALUES
    (1, 77, 'subrogación', 'citation', 'exact');
"""


def _conn_esquema(esquema):
    import psycopg2
    c = psycopg2.connect(DSN, options=f"-c search_path={esquema},public")
    c.autocommit = True
    return c


def _contar(cur, tabla):
    cur.execute(f"SELECT COUNT(*) FROM {tabla}")
    return cur.fetchone()[0]


def test_otro_sitio_en_su_esquema_no_toca_lo_de_bicameral(base, monkeypatch):
    c, _ = base
    with c.cursor() as cur:
        cur.execute("CREATE SCHEMA jurispeed")
    # without its own tables it would read and write Bicameral's: it refuses to start
    with pytest.raises(RuntimeError, match="faltan tablas"):
        Servicio(False, CITAS, 60, 5, contenido=_conn(), sitio=_conn_esquema("jurispeed")).preparar()
    with c.cursor() as cur:
        cur.execute(ESQUEMA_JURISPEED)
    # its tables carry its own names: without the setting it would look for Bicameral's
    with pytest.raises(RuntimeError, match="bicameral_keywords"):
        Servicio(False, CITAS, 60, 5, contenido=_conn(), sitio=_conn_esquema("jurispeed")).preparar()
    with c.cursor() as cur:
        cur.execute("SET search_path = jurispeed, public")
        cur.execute((RAIZ / "sql" / "alertas_sitio.sql").read_text())
        cur.execute("SET search_path = public")
        _citacion_nueva(cur, 401, "Proyecto sobre subrogación de cargos")
        antes = {t: _contar(cur, f"public.{t}") for t in
                 ("bicameral_notifications", "alertas_enviadas", "citation_keyword_notification_sent")}

    bicameral = _escritor()  # holds Bicameral's lock; the other site must still start
    monkeypatch.setenv("SITIO_TABLAS_SIN_PREFIJO", "1")  # from here on, this process is the other site
    jurispeed = Servicio(False, CITAS, 60, 5, contenido=_conn(), sitio=_conn_esquema("jurispeed"))
    jurispeed.preparar()
    assert bicameral.registro_antiguo and not jurispeed.registro_antiguo
    jurispeed.marcas["citaciones_camara"] = 400
    assert jurispeed.una_pasada(ahora=1e9) == 1

    with c.cursor() as cur:
        cur.execute("SELECT user_id, keyword_id, target_id FROM jurispeed.notifications")
        assert cur.fetchall() == [(77, 1, 401)]
        assert _contar(cur, "jurispeed.alertas_enviadas") == 1
        cur.execute("SELECT fuente FROM jurispeed.alertas_estado ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == sorted(CITAS)
        for t, n in antes.items():
            assert _contar(cur, f"public.{t}") == n, t


def test_con_plan_solo_alertan_quienes_lo_tienen(base, monkeypatch):
    """A site with plans (Jurispeed) alerts only users whose current plan
    includes alerts; the rest keep their lists, silent."""
    c, _ = base
    with c.cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM bicameral_keywords ORDER BY 1")
        usuarios = [r[0] for r in cur.fetchall()]
        assert len(usuarios) >= 2
        con, sin = usuarios[0], usuarios[1:]
        cur.execute("CREATE TABLE bicameral_users (id INTEGER PRIMARY KEY, capabilities TEXT[] NOT NULL DEFAULT '{}', "
                    "perfil_evaluado_at TIMESTAMPTZ)")
        cur.execute("INSERT INTO bicameral_users VALUES (%s, ARRAY['congreso','boletin'], NOW())", (con,))
        for i, u in enumerate(sin):
            # one without the capability, one with it but stale, the rest never evaluated
            if i == 0:
                cur.execute("INSERT INTO bicameral_users VALUES (%s, ARRAY['congreso'], NOW())", (u,))
            elif i == 1:
                cur.execute("INSERT INTO bicameral_users VALUES (%s, ARRAY['boletin'], NOW() - INTERVAL '31 days')", (u,))
        todas = leer_listas(cur, cur)
        monkeypatch.setenv("SITIO_CAPACIDAD_ALERTAS", "boletin")
        filtradas = leer_listas(cur, cur)
        cur.execute("DROP TABLE bicameral_users")
    assert {p.user_id for p in todas.palabras} == set(usuarios)
    assert {p.user_id for p in filtradas.palabras} == {con}
    assert {s.user_id for s in filtradas.seguidores} <= {con}
