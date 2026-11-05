"""One site's side: its users' watchlists, the boletín titles for messages, and
(once its source is cut over) writing notifications.

A notification and its alertas_enviadas row go in one transaction, and the
unique key (user_id, regla, objetivo) makes a repeat a no-op, so a restart, a
re-run over the same ids or a user deleting the notification never re-sends it.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from alertas import Alerta, Palabra, Seguimiento


@dataclass(frozen=True)
class Listas:
    palabras: List[Palabra]
    creadas: Dict[int, datetime]  # keyword id -> created_at
    seguidores: List[Seguimiento]
    titulos: Dict[str, str]

    def palabras_vigentes(self, en: Optional[datetime]) -> List[Palabra]:
        """For a backtest: only the keywords that already existed at `en`."""
        if en is None:
            return self.palabras
        en = utc(en)
        return [p for p in self.palabras if p.id not in self.creadas or utc(self.creadas[p.id]) <= en]


def utc(t: datetime) -> datetime:
    """Timestamps without a zone are UTC, as the producers write them."""
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def leer_listas(sitio_cur, contenido_cur) -> Listas:
    sitio_cur.execute("SELECT id, user_id, keyword, match_type, type, created_at FROM bicameral_keywords ORDER BY id")
    palabras, creadas = [], {}
    for id_, user_id, keyword, match_type, tipo, creado in sitio_cur.fetchall():
        palabras.append(Palabra(id_, user_id, keyword, match_type, tipo))
        if creado is not None:
            creadas[id_] = creado
    sitio_cur.execute("SELECT user_id, boletin FROM user_tracked_boletines ORDER BY user_id, boletin")
    seguidores = [Seguimiento(u, b) for u, b in sitio_cur.fetchall()]
    titulos: Dict[str, str] = {}
    boletines = sorted({s.boletin for s in seguidores})
    if boletines:
        contenido_cur.execute("SELECT boletin, titulo FROM boletines WHERE boletin = ANY(%s)", (boletines,))
        titulos = {b: t for b, t in contenido_cur.fetchall()}
    return Listas(palabras, creadas, seguidores, titulos)


COLUMNAS = ("user_id", "keyword_id", "type", "related_id", "chamber", "message", "boletin",
            "origin", "target_type", "target_id", "target_chamber", "target_boletin")


@contextmanager
def _transaccion(conn):
    """One transaction, also on an autocommit connection (all of ours are)."""
    with conn.cursor() as cur:
        cur.execute("BEGIN")
        try:
            yield cur
        except BaseException:
            cur.execute("ROLLBACK")
            raise
        cur.execute("COMMIT")


def hay_registro_antiguo(cur) -> bool:
    """Whether this site still has the citation scrapers' dedupe tables
    (Bicameral, until its citation alerts are fully handed over)."""
    cur.execute("SELECT to_regclass('citation_keyword_notification_sent') IS NOT NULL "
                "AND to_regclass('citation_boletin_notification_sent') IS NOT NULL")
    return bool(cur.fetchone()[0])


def _cita_antigua(a: Alerta) -> Optional[tuple]:
    """(kind, user, keyword or boletín, chamber, citation id) for a citation alert."""
    tipo, _, camara_id = a.objetivo.partition(":")
    if tipo != "citation":
        return None
    camara, _, cid = camara_id.partition(":")
    if camara not in ("senate", "camara"):
        return None
    clase, _, valor = a.regla.partition(":")
    return clase, a.user_id, valor, camara, int(cid)


def _ya_en_registro_antiguo(cur, cita: tuple) -> bool:
    clase, user_id, valor, _, cid = cita
    if clase == "keyword":
        cur.execute("SELECT 1 FROM citation_keyword_notification_sent "
                    "WHERE user_id = %s AND keyword_id = %s AND citation_id = %s LIMIT 1", (user_id, int(valor), cid))
    else:
        cur.execute("SELECT 1 FROM citation_boletin_notification_sent "
                    "WHERE user_id = %s AND citation_id = %s AND boletin = %s LIMIT 1", (user_id, cid, valor))
    return cur.fetchone() is not None


def _anotar_en_registro_antiguo(cur, cita: tuple) -> None:
    clase, user_id, valor, camara, cid = cita
    tabla = f"{camara}_citations"  # camara is 'senate' or 'camara' (_cita_antigua)
    if clase == "keyword":
        cur.execute(f"INSERT INTO citation_keyword_notification_sent (user_id, keyword_id, citation_id, citation_date) "
                    f"SELECT %s, %s, id, date FROM {tabla} WHERE id = %s ON CONFLICT DO NOTHING",
                    (user_id, int(valor), cid))
    else:
        cur.execute(f"INSERT INTO citation_boletin_notification_sent (user_id, citation_id, boletin, citation_date) "
                    f"SELECT %s, id, %s, date FROM {tabla} WHERE id = %s ON CONFLICT DO NOTHING",
                    (user_id, valor, cid))


def escribir(conn, alertas: Iterable[Alerta], registro_antiguo: bool = False) -> int:
    """Insert each alert's notification unless its key was already sent. Returns
    how many were written.

    With registro_antiguo (Bicameral during the citation handover) a citation
    alert also counts as sent when the scrapers' own dedupe tables have it, and
    is recorded there too. The scraper and the service then share one record,
    so either can run, or both for a while, without sending anything twice."""
    escritas = 0
    for a in alertas:
        cols = [c for c in COLUMNAS if c in a.fila]
        cita = _cita_antigua(a) if registro_antiguo else None
        with _transaccion(conn) as cur:  # one transaction per alert
            cur.execute(
                "INSERT INTO alertas_enviadas (user_id, regla, objetivo) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING user_id", (a.user_id, a.regla, a.objetivo))
            if cur.fetchone() is None:
                continue
            if cita and _ya_en_registro_antiguo(cur, cita):
                continue  # the scraper sent it; alertas_enviadas now remembers that too
            cur.execute(
                f"INSERT INTO bicameral_notifications ({', '.join(cols)}, created_at) "
                f"VALUES ({', '.join(['%s'] * len(cols))}, TIMEZONE('utc', NOW())) RETURNING id",
                [a.fila[c] for c in cols])
            nid = cur.fetchone()[0]
            cur.execute("UPDATE alertas_enviadas SET notification_id = %s "
                        "WHERE user_id = %s AND regla = %s AND objetivo = %s",
                        (nid, a.user_id, a.regla, a.objetivo))
            if cita:
                _anotar_en_registro_antiguo(cur, cita)
            escritas += 1
    return escritas


def leer_marcas(cur, fuentes: Sequence[str]) -> Dict[str, int]:
    cur.execute("SELECT fuente, marca FROM alertas_estado WHERE fuente = ANY(%s)", (list(fuentes),))
    return {f: int(m) for f, m in cur.fetchall()}


def guardar_marca(cur, fuente: str, marca: int) -> None:
    cur.execute(
        "INSERT INTO alertas_estado (fuente, marca, actualizado_en) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (fuente) DO UPDATE SET marca = EXCLUDED.marca, actualizado_en = NOW()", (fuente, marca))
