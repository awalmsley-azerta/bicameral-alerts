"""Connections: the content database (Bicameral's, read only) and one site's
database (its users' watchlists and notifications).

For Bicameral both are the same Aurora database, so SITIO_DB_* defaults to the
DB_* values. Another site may share that database with its own schema
(SITIO_DB_ESQUEMA): its connection then searches that schema first, and public
(the content) after it.
"""
from __future__ import annotations

import json
import os


def _datos(prefijo: str, respaldo: str | None = None) -> dict:
    def env(nombre, defecto=None):
        valor = os.getenv(f"{prefijo}{nombre}")
        if valor is None and respaldo is not None:
            valor = os.getenv(f"{respaldo}{nombre}")
        return valor if valor is not None else defecto

    secreto = env("SECRET_JSON")
    creds = json.loads(secreto) if secreto else {}
    return {
        "host": env("HOST", "localhost"), "port": env("PORT", "5432"),
        "dbname": creds.get("dbname") or env("NAME", "bicameral"),
        "user": creds.get("username") or env("USER", "bicameral"),
        "password": creds.get("password") or env("PASSWORD", ""),
    }


def _opciones(prefijo: str) -> dict:
    esquema = (os.getenv(f"{prefijo}ESQUEMA") or "").strip()
    if not esquema:
        return {}
    if not esquema.replace("_", "").isalnum() or not esquema[0].isalpha():
        raise ValueError(f"{prefijo}ESQUEMA no es un nombre de esquema válido: {esquema!r}")
    return {"options": f"-c search_path={esquema},public"}


def conectar(prefijo: str = "DB_", respaldo: str | None = None, *, solo_lectura: bool = False):
    """A psycopg2 connection. With solo_lectura every transaction is READ ONLY,
    so a dry run cannot write even by mistake."""
    import psycopg2
    conn = psycopg2.connect(connect_timeout=10, application_name="bicameral-alertas", **_datos(prefijo, respaldo),
                            **_opciones(prefijo))
    conn.autocommit = True
    if solo_lectura:
        with conn.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


def contenido(solo_lectura: bool = True):
    return conectar("DB_", solo_lectura=solo_lectura)


def sitio(solo_lectura: bool = False):
    return conectar("SITIO_DB_", "DB_", solo_lectura=solo_lectura)
