"""The always-on loop: read new content, match it against the site's watchlists,
and log (--simular) or write the alerts.

Live turns are read every few seconds, everything else every minute. With
--simular nothing is written anywhere: both connections are read only, the
watermarks live in memory and start at the newest row, and each alert is one
ALERTA_SIMULADA log line. Writing mode keeps the watermarks in alertas_estado
and holds a Postgres advisory lock, so a second copy waits instead of doubling.

Only the sources named in ALERTAS_FUENTES are read (all by default), so a
cutover can move one source at a time.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List

from servicio import db, fuentes as F
from servicio.simular import alertas_de
from servicio.sitio import escribir, guardar_marca, leer_listas, leer_marcas

logger = logging.getLogger(__name__)
CERROJO = 7417026  # pg advisory lock key for one site's writer
LOTE = 200


class Servicio:
    def __init__(self, simular: bool, nombres: List[str], intervalo: float, intervalo_turnos: float,
                 refresco_listas: float = 60.0, contenido=None, sitio=None):
        self.simular = simular
        self.fuentes = [f for f in F.todas() if f.nombre in nombres]
        self.intervalo, self.intervalo_turnos, self.refresco = intervalo, intervalo_turnos, refresco_listas
        self.contenido = contenido or db.contenido(solo_lectura=True)
        self.sitio = sitio or db.sitio(solo_lectura=simular)
        self.marcas: Dict[str, int] = {}
        self.enviadas = set()  # dry run only; writing mode uses alertas_enviadas
        self._listas, self._listas_en = None, 0.0
        self._proxima: Dict[str, float] = {}
        self.parar = False

    def preparar(self) -> None:
        if not self.simular:
            with self.sitio.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (CERROJO,))
                if not cur.fetchone()[0]:
                    raise RuntimeError("otra copia del servicio tiene el cerrojo de este sitio")
                guardadas = leer_marcas(cur, [f.nombre for f in self.fuentes])
        else:
            guardadas = {}
        with self.contenido.cursor() as cur:
            for f in self.fuentes:
                # a source never seen starts at its newest row: no backfill of history
                self.marcas[f.nombre] = guardadas.get(f.nombre, f.ultimo_id(cur))
        logger.info("ALERTAS_INICIO simular=%s marcas=%s", self.simular, self.marcas)

    def listas(self):
        if self._listas is None or time.monotonic() - self._listas_en > self.refresco:
            with self.sitio.cursor() as s, self.contenido.cursor() as c:
                self._listas = leer_listas(s, c)
            self._listas_en = time.monotonic()
        return self._listas

    def una_pasada(self, ahora: float | None = None) -> int:
        """Read every source that is due once. Returns the alerts produced."""
        ahora = time.monotonic() if ahora is None else ahora
        total = 0
        for f in self.fuentes:
            if self._proxima.get(f.nombre, 0) > ahora:
                continue
            self._proxima[f.nombre] = ahora + (self.intervalo_turnos if f.nombre == "turnos" else self.intervalo)
            total += self._fuente(f)
        return total

    def _fuente(self, f) -> int:
        with self.contenido.cursor() as cur:
            lote, marca = f.leer(cur, self.marcas[f.nombre], LOTE, datetime.now(timezone.utc))
        if not lote:
            return 0
        listas = self.listas()
        alertas = [a for _, creado, item in lote for a in alertas_de(f.nombre, item, creado, listas)]
        if self.simular:
            n = 0
            for a in alertas:
                clave = (a.user_id, a.regla, a.objetivo)
                if clave in self.enviadas:
                    continue
                self.enviadas.add(clave)
                n += 1
                logger.info("ALERTA_SIMULADA %s", json.dumps(
                    {"fuente": f.nombre, "user_id": a.user_id, "regla": a.regla, "objetivo": a.objetivo,
                     "fila": a.fila}, ensure_ascii=False, default=str))
        else:
            n = escribir(self.sitio, alertas)
            with self.sitio.cursor() as cur:
                guardar_marca(cur, f.nombre, marca)
        self.marcas[f.nombre] = marca
        logger.info("ALERTAS_LOTE fuente=%s items=%d alertas=%d marca=%d", f.nombre, len(lote), n, marca)
        return n

    def correr(self) -> None:
        self.preparar()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "parar", True))
        while not self.parar:
            try:
                self.una_pasada()
            except Exception:
                # one bad pass must not stop alerts; the metric filter alarms on this line
                logger.exception("ALERTA_FALLIDA")
                time.sleep(self.intervalo_turnos)
            time.sleep(1)
        logger.info("ALERTAS_FIN marcas=%s", self.marcas)


def nombres_de_entorno() -> List[str]:
    todas = [f.nombre for f in F.todas()]
    pedidas = [n.strip() for n in os.getenv("ALERTAS_FUENTES", "").split(",") if n.strip()]
    desconocidas = sorted(set(pedidas) - set(todas))
    if desconocidas:
        raise ValueError(f"ALERTAS_FUENTES desconocidas: {desconocidas} (válidas: {todas})")
    return pedidas or todas
