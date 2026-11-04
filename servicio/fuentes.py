"""New content, read from the Bicameral database by id.

Each source is one table read in id order after a watermark (the last id seen),
so a run never needs a queue or a change to the producers. Every source yields
(id, creado_en, item) with item ready for alertas.reglas.

Citations are also re-read when they are edited (sql/citaciones_editadas.sql
stamps updated_at), as the scrapers re-check an edited citation today.

Boletín movements are not a source yet: the scraper still notifies directly
and keeps no record of movements (boletin_movimientos comes with its cutover).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

from alertas import Citacion, Sesion, Turno
from servicio.sitio import utc

# A session row written without its analysis waits this long for it before it is
# matched with what it has (analyse fills analysis_text on a re-run).
ESPERA_ANALISIS = timedelta(hours=6)
# An edit is read once it is this old, so a slower transaction stamped earlier
# cannot commit behind the watermark.
ASIENTO_EDICION = timedelta(minutes=1)


@dataclass(frozen=True)
class Fuente:
    nombre: str
    tabla: str
    sql: str
    armar: Callable[[tuple], object]
    lista: Callable[[tuple, datetime], bool] = lambda fila, ahora: True

    def leer(self, cur, despues_de: int, limite: int, ahora: Optional[datetime] = None) -> Tuple[List[tuple], int]:
        """Up to `limite` items after id `despues_de`, and the new watermark.
        The watermark stops before the first row that is not ready yet."""
        ahora = ahora or datetime.now(timezone.utc)
        cur.execute(self.sql, (despues_de, limite))
        listos, marca = [], despues_de
        for fila in cur.fetchall():
            if not self.lista(fila, ahora):
                break
            listos.append((fila[0], fila[-1], self.armar(fila)))
            marca = fila[0]
        return listos, marca

    def primer_id_desde(self, cur, desde: datetime) -> int:
        """The watermark that makes a backtest start at `desde`."""
        cur.execute(f"SELECT COALESCE(MIN(id), (SELECT COALESCE(MAX(id), 0) + 1 FROM {self.tabla})) - 1 "
                    f"FROM {self.tabla} WHERE created_at >= %s", (desde,))
        return cur.fetchone()[0]

    def ultimo_id(self, cur) -> int:
        cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {self.tabla}")
        return cur.fetchone()[0]


def _us(t: datetime) -> int:
    """Edited sources keep their watermark as microseconds since the epoch."""
    return int(utc(t).timestamp() * 1_000_000)


def _desde_us(us: int) -> datetime:
    return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)


@dataclass(frozen=True)
class FuenteEditadas(Fuente):
    """Citations edited after they were written, in updated_at order. The item's
    time is its edit, so the 2-day citation cutoff counts from the edit, as when
    the scrapers re-checked it. A citation edited twice is matched twice; only
    new matches alert (alertas_enviadas)."""

    def leer(self, cur, despues_de: int, limite: int, ahora: Optional[datetime] = None) -> Tuple[List[tuple], int]:
        ahora = ahora or datetime.now(timezone.utc)
        cur.execute(self.sql, (_desde_us(despues_de), ahora - ASIENTO_EDICION, limite))
        filas = cur.fetchall()
        listos = [(f[0], f[-1], self.armar(f)) for f in filas]
        marca = max([despues_de] + [_us(f[-1]) for f in filas])
        if len(filas) == limite:
            marca -= 1  # rows stamped in the same microsecond may be past the limit: read them again
        return listos, marca

    def primer_id_desde(self, cur, desde: datetime) -> int:
        return _us(desde)

    def ultimo_id(self, cur) -> int:
        cur.execute(f"SELECT COALESCE(MAX(updated_at), NOW()) FROM {self.tabla}")
        return _us(cur.fetchone()[0])


def _sesion(camara):
    def armar(f):
        (id_, commission, commission_original, title, materia, boletin, fecha, hora,
         transcript, analysis, _creado) = f
        return Sesion(id=id_, camara=camara, commission=commission or "", commission_original=commission_original or "",
                      title=title or "", materia=materia or "", boletin=boletin or "",
                      date=fecha.isoformat() if fecha else None, time=_hora(hora),
                      transcript_text=transcript, analysis_text=analysis)
    return armar


def _hora(valor) -> str:
    """'15:00' as analyse passes it, not the database's '15:00:00'."""
    if not valor:
        return ""
    return valor.strftime("%H:%M") if hasattr(valor, "strftime") else str(valor)


def _con_analisis(fila, ahora) -> bool:
    return bool(fila[9]) or (ahora - utc(fila[-1])) > ESPERA_ANALISIS


def _citacion(camara):
    def armar(f):
        id_, commission, commission_original, materia, boletin, fecha, hora, _creado = f
        return Citacion(id=id_, camara=camara, commission=commission or "", commission_original=commission_original or "",
                        materia=materia or "", boletin=boletin or "", date=fecha.isoformat() if fecha else None,
                        time=_hora(hora))
    return armar


def _turno(f):
    id_, camara, plenario, inicio, texto, _creado = f
    return Turno(plenario_id=plenario, camara=camara, texto=texto or "", inicio_s=float(inicio or 0))


_SESIONES = {
    # Senate rows keep no long commission name; the citation's is the closest.
    "senate": ("senate_sessions",
               "SELECT s.id, s.commission, c.commission_original, s.title, s.materia, s.boletin, s.date, s.time, "
               "s.transcript_text, s.analysis_text, s.created_at FROM senate_sessions s "
               "LEFT JOIN senate_citations c ON c.id = s.citation_id WHERE s.id > %s ORDER BY s.id LIMIT %s"),
    "camara": ("camara_sessions",
               "SELECT id, commission, commission_original, title, materia, boletin, date, time, "
               "transcript_text, analysis_text, created_at FROM camara_sessions WHERE id > %s ORDER BY id LIMIT %s"),
}


def todas(editadas: bool = True) -> List[Fuente]:
    """Every source. A backtest leaves the edited ones out: only a citation's last
    version is kept, so its edits cannot be replayed."""
    fuentes = [Fuente(f"sesiones_{c}", tabla, sql, _sesion(c), _con_analisis) for c, (tabla, sql) in _SESIONES.items()]
    for c in ("senate", "camara"):
        fuentes.append(Fuente(
            f"citaciones_{c}", f"{c}_citations",
            f"SELECT id, commission, commission_original, materia, boletin, date, time, created_at "
            f"FROM {c}_citations WHERE id > %s ORDER BY id LIMIT %s", _citacion(c)))
    fuentes.append(Fuente(
        "turnos", "plenary_turns",
        "SELECT id, chamber, plenary_id, start_seconds, text, created_at FROM plenary_turns "
        "WHERE id > %s ORDER BY id LIMIT %s", _turno))
    if editadas:
        for c in ("senate", "camara"):
            fuentes.append(FuenteEditadas(
                f"citaciones_{c}_editadas", f"{c}_citations",
                f"SELECT id, commission, commission_original, materia, boletin, date, time, updated_at "
                f"FROM {c}_citations WHERE updated_at > %s AND updated_at < %s ORDER BY updated_at, id LIMIT %s",
                _citacion(c)))
    return fuentes
