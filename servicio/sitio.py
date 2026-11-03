"""One site's side: its users' watchlists, the boletín titles for messages, and
(once its source is cut over) writing notifications.

A notification and its alertas_enviadas row go in one transaction, and the
unique key (user_id, regla, objetivo) makes a repeat a no-op, so a restart, a
re-run over the same ids or a user deleting the notification never re-sends it.
"""
from __future__ import annotations

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


def escribir(conn, alertas: Iterable[Alerta]) -> int:
    """Insert each alert's notification unless its key was already sent. Returns
    how many were written."""
    escritas = 0
    for a in alertas:
        cols = [c for c in COLUMNAS if c in a.fila]
        with conn:  # one transaction per alert
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO alertas_enviadas (user_id, regla, objetivo) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING RETURNING user_id", (a.user_id, a.regla, a.objetivo))
                if cur.fetchone() is None:
                    continue
                cur.execute(
                    f"INSERT INTO bicameral_notifications ({', '.join(cols)}, created_at) "
                    f"VALUES ({', '.join(['%s'] * len(cols))}, TIMEZONE('utc', NOW())) RETURNING id",
                    [a.fila[c] for c in cols])
                nid = cur.fetchone()[0]
                cur.execute("UPDATE alertas_enviadas SET notification_id = %s "
                            "WHERE user_id = %s AND regla = %s AND objetivo = %s",
                            (nid, a.user_id, a.regla, a.objetivo))
                escritas += 1
    return escritas


def leer_marcas(cur, fuentes: Sequence[str]) -> Dict[str, int]:
    cur.execute("SELECT fuente, marca FROM alertas_estado WHERE fuente = ANY(%s)", (list(fuentes),))
    return {f: int(m) for f, m in cur.fetchall()}


def guardar_marca(cur, fuente: str, marca: int) -> None:
    cur.execute(
        "INSERT INTO alertas_estado (fuente, marca, actualizado_en) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (fuente) DO UPDATE SET marca = EXCLUDED.marca, actualizado_en = NOW()", (fuente, marca))
