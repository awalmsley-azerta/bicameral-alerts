"""Record what today's producers write for tests/casos.py, as tests/golden/esperado.json.

Runs the real functions from each producer's origin/main (read with `git show`,
so the checkouts' own branches don't matter) against a fake database that
answers their queries from the cases and records every bicameral_notifications
row they insert:

  analyse   main.py                         _check_session_keywords
  scrapers  scrape/citations/db_utils.py    _check_citation_keywords
  scrapers  scrape/boletines/db_utils.py    check_boletin_movement_and_notify
  live      alert_writer.py + db.py         AlertWriter.on_final_turn

Only those functions and the helpers they call are loaded (by name, from the
AST), so none of the producers' dependencies are needed. A query the fake does
not know, or a warning the old code would swallow, stops the recording.

    python scripts/grabar_golden.py --repos ..   # folder holding bicameral-analyse, -scrapers, -live
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import types
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from tests import casos  # noqa: E402

FUENTES = {
    "analyse": ("bicameral-analyse", "main.py",
                ["_normalize_text_for_matching", "_extract_hit_sentence", "_find_keyword_context",
                 "_count_keyword_occurrences", "_boletin_variants", "_short_titulo", "_keyword_matches",
                 "_check_session_keywords"]),
    "citaciones": ("bicameral-scrapers", "scrape/citations/db_utils.py",
                   ["_normalize_text_for_matching", "_extract_hit_sentence", "_find_keyword_context",
                    "_count_keyword_occurrences", "_boletin_variants", "_short_titulo", "_keyword_matches",
                    "_check_citation_keywords"]),
    "boletines": ("bicameral-scrapers", "scrape/boletines/db_utils.py",
                  ["_short_titulo", "_count_tramites_in_xml", "check_boletin_movement_and_notify"]),
    "live_db": ("bicameral-live", "db.py", ["insert_notification", "load_active_keywords"]),
    "live_matcher": ("bicameral-live", "keyword_matcher.py", None),
    "live_writer": ("bicameral-live", "alert_writer.py", None),
}


class Silenciado(RuntimeError):
    """The old code hit an error it would have logged and swallowed."""


class _Registro:
    def __getattr__(self, nivel):
        if nivel in ("warning", "error", "exception"):
            def falla(msg, *args, **kw):
                raise Silenciado(msg % args if args else msg)
            return falla
        return lambda *a, **k: None


class _Diccionario:
    """Stands in for psycopg2.extras.RealDictCursor: rows come back as dicts."""


class BaseFalsa:
    def __init__(self, antes: int = 0):
        self.filas: List[dict] = []
        self.enviadas: set = set()
        self.antes = antes

    def conexion(self):
        base = self

        class Conexion:
            autocommit = True
            closed = False

            def cursor(self, cursor_factory=None):
                return Cursor(base, cursor_factory is not None)

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        return Conexion()


class Cursor:
    def __init__(self, base: BaseFalsa, como_dict: bool):
        self.base, self.como_dict, self._r = base, como_dict, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _filas(self, filas, columnas):
        return [dict(zip(columnas, f)) if self.como_dict else tuple(f) for f in filas]

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        p = list(params or [])
        if q.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")) or \
                q.startswith("INSERT INTO boletin_tracking_state"):
            self._r = []
        elif "FROM bicameral_keywords" in q:
            tipos = {"session", "both"} if "'session'" in q else {"citation", "both"}
            self._r = self._filas([(i, u, k, m) for i, u, k, m, t in casos.PALABRAS if t in tipos],
                                  ["id", "user_id", "keyword", "match_type"])
        elif q.startswith("SELECT user_id, boletin FROM user_tracked_boletines") and "alert_on_mentions" in q:
            # unified boletín alerts: every follower gets the mentions
            self._r = self._filas(casos.SEGUIDORES, ["user_id", "boletin"])
        elif q.startswith("SELECT user_id FROM user_tracked_boletines WHERE boletin"):
            self._r = self._filas([(u,) for u, b in casos.SEGUIDORES if b == p[0]], ["user_id"])
        elif q.startswith("SELECT boletin, titulo FROM boletines WHERE boletin = ANY"):
            self._r = self._filas([(b, casos.TITULOS[b]) for b in p[0] if b in casos.TITULOS], ["boletin", "titulo"])
        elif q.startswith("SELECT titulo FROM boletines WHERE boletin"):
            self._r = self._filas([(casos.TITULOS[p[0]],)] if p[0] in casos.TITULOS else [], ["titulo"])
        elif q.startswith("SELECT last_tramite_count FROM boletin_tracking_state"):
            self._r = self._filas([(self.base.antes,)] if self.base.antes else [], ["last_tramite_count"])
        elif q.startswith("SELECT 1 FROM bicameral_notifications"):
            u, b, sid = p
            hay = any(f["user_id"] == u and f.get("boletin") == b and f.get("target_type") == "session"
                      and f.get("target_id") == sid and f["origin"] == "keyword_match" for f in self.base.filas)
            self._r = [(1,)] if hay else []
        elif q.startswith("SELECT 1 FROM citation_keyword_notification_sent"):
            self._r = [(1,)] if ("kw",) + tuple(p) in self.base.enviadas else []
        elif q.startswith("SELECT 1 FROM citation_boletin_notification_sent"):
            self._r = [(1,)] if ("bol",) + tuple(p) in self.base.enviadas else []
        elif q.startswith("INSERT INTO citation_keyword_notification_sent"):
            self.base.enviadas.add(("kw",) + tuple(p[:3]))
            self._r = []
        elif q.startswith("INSERT INTO citation_boletin_notification_sent"):
            self.base.enviadas.add(("bol",) + tuple(p[:3]))
            self._r = []
        elif q.startswith("INSERT INTO bicameral_notifications"):
            self.base.filas.append(_fila(q, p))
            self._r = []
        else:
            raise AssertionError(f"consulta desconocida: {q[:120]}")

    def fetchall(self):
        return list(self._r)

    def fetchone(self):
        return self._r[0] if self._r else None


def _partes(lista: str) -> List[str]:
    """Split a SQL value list on top-level commas."""
    partes, nivel, actual = [], 0, ""
    for c in lista:
        if c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
        if c == "," and nivel == 0:
            partes.append(actual.strip())
            actual = ""
        else:
            actual += c
    return partes + [actual.strip()]


def _fila(q: str, params: list) -> dict:
    m = re.match(r"INSERT INTO bicameral_notifications \((.*?)\) VALUES \((.*)\)$", q)
    columnas = [c.strip() for c in m.group(1).split(",")]
    valores = _partes(m.group(2))
    assert len(columnas) == len(valores), (columnas, valores)
    params = list(params)
    fila = {}
    for col, val in zip(columnas, valores):
        if val == "%s":
            fila[col] = params.pop(0)
        elif val == "NULL":
            fila[col] = None
        elif val.startswith("'"):
            fila[col] = val.strip("'")
        elif re.fullmatch(r"-?\d+", val):
            fila[col] = int(val)
        elif col == "created_at":
            continue
        else:
            raise AssertionError(f"valor desconocido {col}={val}")
    assert not params, params
    return fila


def _fuente(repos: Path, repo: str, ruta: str, ref: str) -> str:
    return subprocess.run(["git", "-C", str(repos / repo), "show", f"{ref}:{ruta}"],
                          check=True, capture_output=True, text=True).stdout


def _commit(repos: Path, repo: str, ref: str) -> str:
    return subprocess.run(["git", "-C", str(repos / repo), "rev-parse", "--short", ref],
                          check=True, capture_output=True, text=True).stdout.strip()


def _funciones(codigo: str, nombres: List[str], espacio: dict) -> dict:
    arbol = ast.parse(codigo)
    defs = {n.name: n for n in arbol.body if isinstance(n, ast.FunctionDef)}
    faltan = [n for n in nombres if n not in defs]
    assert not faltan, f"no están en origin/main: {faltan}"
    for n in nombres:
        exec(compile(ast.Module(body=[defs[n]], type_ignores=[]), f"<{n}>", "exec"), espacio)
    return espacio


def _modulo(nombre: str, codigo: str) -> types.ModuleType:
    mod = types.ModuleType(nombre)
    sys.modules[nombre] = mod
    exec(compile(codigo, f"<{nombre}>", "exec"), mod.__dict__)
    return mod


class _HoyFijo(date):
    @classmethod
    def today(cls):
        return casos.HOY


def grabar(repos: Path, ref: str) -> dict:
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = _Diccionario
    extras.execute_values = None
    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.extras = extras
    sys.modules.update({"psycopg2": psycopg2, "psycopg2.extras": extras})
    comun = {"re": re, "json": json, "logger": _Registro(), "logging": logging, "unicodedata": unicodedata,
             "os": os, "ET": ET, "Optional": Optional, "Dict": Dict, "List": List}
    codigo = {k: _fuente(repos, repo, ruta, ref) for k, (repo, ruta, _) in FUENTES.items()}

    # sessions
    analyse = _funciones(codigo["analyse"], FUENTES["analyse"][2], dict(comun))
    sesiones = {}
    for s in casos.sesiones():
        base = BaseFalsa()
        datos = {k: s[k] for k in ("commission", "commission_original", "title", "materia", "boletin", "date", "time")}
        analyse["_check_session_keywords"](base.conexion(), s["id"], datos, transcript_text=s["transcript_text"],
                                           analysis_text=s["analysis_text"], chamber=s["camara"])
        sesiones[str(s["id"])] = base.filas

    # citations
    citas = _funciones(codigo["citaciones"], FUENTES["citaciones"][2],
                       dict(comun, _date_type=_HoyFijo, timedelta=timedelta))
    citaciones = {}
    for c in casos.citaciones():
        base = BaseFalsa()
        datos = {k: c[k] for k in ("commission", "commission_original", "materia", "boletin", "date", "time")}
        citas["_check_citation_keywords"](base.conexion(), c["id"], datos, c["camara"])
        citaciones[str(c["id"])] = base.filas

    # boletín movements
    bol = _funciones(codigo["boletines"], FUENTES["boletines"][2], dict(comun))
    os.environ.setdefault("DB_HOST", "falso")
    movimientos = []
    for boletin, antes, despues in casos.MOVIMIENTOS:
        base = BaseFalsa(antes=antes)
        bol["get_db_connection"] = base.conexion
        bol["check_boletin_movement_and_notify"](boletin, casos.xml_tramites(despues))
        movimientos.append(base.filas)

    # live plenary turns: one AlertWriter per plenary, turns in order
    base = BaseFalsa()

    @contextlib.contextmanager
    def cursor(cfg):
        yield Cursor(base, False)

    config = types.ModuleType("config")
    config.Config = type("Config", (), {})
    sys.modules["config"] = config
    db = types.ModuleType("db")
    db.__dict__.update(comun, cursor=cursor, Config=config.Config)
    _funciones(codigo["live_db"], FUENTES["live_db"][2], db.__dict__)
    sys.modules["db"] = db
    _modulo("keyword_matcher", codigo["live_matcher"])
    escritor = _modulo("alert_writer", codigo["live_writer"])
    escritor.logger = _Registro()
    escritores = {}
    turnos = []
    for camara, plenario, texto, inicio in casos.turnos():
        clave = (camara, plenario)
        if clave not in escritores:
            escritores[clave] = escritor.AlertWriter(config.Config(), escritor.PlenarySession(camara, plenario, f"run-{plenario}"))
        antes = len(base.filas)
        escritores[clave].on_final_turn(text=texto, turn_start_seconds=inicio)
        turnos.append(base.filas[antes:])

    return {
        "fuentes": {repo: _commit(repos, repo, ref) for repo in sorted({r for r, _, _ in FUENTES.values()})},
        "ref": ref,
        "hoy": casos.HOY.isoformat(),
        "sesiones": sesiones,
        "citaciones": citaciones,
        "movimientos": movimientos,
        "turnos": turnos,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos", default=str(RAIZ.parent), help="folder holding the producer repositories")
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--salida", default=str(RAIZ / "tests" / "golden" / "esperado.json"))
    a = ap.parse_args()
    esperado = grabar(Path(a.repos), a.ref)
    Path(a.salida).write_text(json.dumps(esperado, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    n = sum(len(v) for v in esperado["sesiones"].values()) + sum(len(v) for v in esperado["citaciones"].values()) + \
        sum(len(v) for v in esperado["movimientos"]) + sum(len(v) for v in esperado["turnos"])
    print(f"{n} notificaciones grabadas en {a.salida} desde {esperado['fuentes']}")


if __name__ == "__main__":
    main()
