"""Backtest: replay a past window of content through alertas and compare with the
notifications the producers actually wrote for the same items.

Read only on both databases. Keywords created after an item are left out for
that item, so today's watchlists approximate the ones of the time. What cannot
be reconstructed (keywords since deleted, notifications users deleted) is
reported as such, not as a mismatch.

Boletín movements are replayed from boletin_movimientos once it has rows (the
scraper writes it from its cutover on) and compared as counts per follower and
boletín, as plenaries are: a movement's notification names no item.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from alertas import Alerta, alertas_citacion, alertas_movimiento, alertas_sesion, alertas_turno
from servicio import fuentes as F
from servicio.sitio import Listas, hay_registro_antiguo, leer_listas, utc

logger = logging.getLogger(__name__)
LOTE = 500
EJEMPLOS = 10


@dataclass
class Corrida:
    alertas: List[Tuple[str, Alerta]] = field(default_factory=list)  # (source, alert)
    items: Dict[str, List[int]] = field(default_factory=lambda: defaultdict(list))
    plenarios: Dict[str, set] = field(default_factory=lambda: defaultdict(set))  # chamber -> plenary ids
    boletines: set = field(default_factory=set)  # boletines that moved
    desde: Optional[datetime] = None
    hasta: Optional[datetime] = None


def alertas_de(fuente: str, item, creado: datetime, listas: Listas) -> List[Alerta]:
    palabras = listas.palabras_vigentes(creado)
    if fuente.startswith("sesiones_"):
        return alertas_sesion(item, palabras, listas.seguidores, listas.titulos)
    if fuente.startswith("citaciones_"):
        # the producers compared the citation date with the day they ran
        return alertas_citacion(item, palabras, listas.seguidores, listas.titulos, utc(creado).date())
    if fuente == "movimientos":
        return alertas_movimiento(item, listas.seguidores, listas.titulos.get(item.boletin))
    return alertas_turno(item, palabras)


def recorrer(contenido_cur, listas: Listas, desde: datetime, hasta: Optional[datetime]) -> Corrida:
    corrida, enviadas = Corrida(desde=desde, hasta=hasta), set()
    for fuente in F.todas(editadas=False):
        if not fuente.existe(contenido_cur):
            logger.info("backtest %s: no table yet", fuente.nombre)
            continue
        marca, n = fuente.primer_id_desde(contenido_cur, desde), 0
        while True:
            lote, nueva = fuente.leer(contenido_cur, marca, LOTE)
            if not lote:
                break
            fin = False
            for id_, creado, item in lote:
                if hasta is not None and utc(creado) >= utc(hasta):
                    fin = True
                    break
                n += 1
                if fuente.nombre == "turnos":
                    corrida.plenarios[item.camara].add(item.plenario_id)
                elif fuente.nombre == "movimientos":
                    corrida.boletines.add(item.boletin)
                else:
                    corrida.items[fuente.nombre].append(id_)
                for a in alertas_de(fuente.nombre, item, creado, listas):
                    clave = (a.user_id, a.regla, a.objetivo)
                    if clave not in enviadas:  # alertas_enviadas, in memory
                        enviadas.add(clave)
                        corrida.alertas.append((fuente.nombre, a))
            if fin or nueva == marca:
                break
            marca = nueva
        logger.info("backtest %s: %d items", fuente.nombre, n)
    return corrida


# -- comparison ------------------------------------------------------------------

def _clave(f: dict) -> Optional[tuple]:
    """What identifies a notification across old and new, ignoring its message."""
    if f["type"] == "plenary":
        return ("plenario", f["user_id"], f["keyword_id"], f["target_chamber"], f["target_id"])
    if f.get("origin") == "boletin_movement":
        return ("movimiento", f["user_id"], f["target_boletin"])
    if f.get("keyword_id") is not None:
        return ("palabra", f["user_id"], f["keyword_id"], f["target_type"], f["target_chamber"], f["target_id"])
    return ("mencion", f["user_id"], f.get("boletin"), f["target_type"], f["target_chamber"], f["target_id"])


def notificaciones_antiguas(sitio_cur, corrida: Corrida) -> List[dict]:
    columnas = ["id", "user_id", "keyword_id", "type", "related_id", "chamber", "message", "boletin", "origin",
                "target_type", "target_id", "target_chamber", "target_boletin", "created_at"]
    filas = []
    consultas = []
    for nombre, ids in corrida.items.items():
        tipo, camara = nombre.split("_")
        consultas.append(("target_type = %s AND target_chamber = %s AND target_id = ANY(%s)",
                          ("session" if tipo == "sesiones" else "citation", camara, ids)))
    for camara, plenarios in corrida.plenarios.items():
        consultas.append(("type = 'plenary' AND target_chamber = %s AND target_id = ANY(%s)", (camara, list(plenarios))))
    if corrida.boletines:
        # a movement's notification names only its boletín: take the window's
        donde = "origin = 'boletin_movement' AND target_boletin = ANY(%s) AND created_at >= %s"
        params = [sorted(corrida.boletines), utc(corrida.desde).replace(tzinfo=None)]
        if corrida.hasta is not None:
            donde += " AND created_at < %s"
            params.append(utc(corrida.hasta).replace(tzinfo=None))
        consultas.append((donde, tuple(params)))
    for donde, params in consultas:
        sitio_cur.execute(f"SELECT {', '.join(columnas)} FROM bicameral_notifications WHERE {donde}", params)
        filas += [dict(zip(columnas, f)) for f in sitio_cur.fetchall()]
    return filas


def _motivos(sitio_cur) -> dict:
    sitio_cur.execute("SELECT id FROM bicameral_keywords")
    palabras = {r[0] for r in sitio_cur.fetchall()}
    sitio_cur.execute("SELECT user_id, boletin, COALESCE(alert_on_mentions, FALSE) FROM user_tracked_boletines")
    seguidos = {(u, b): m for u, b, m in sitio_cur.fetchall()}
    cit_kw, cit_bol = set(), set()
    if hay_registro_antiguo(sitio_cur):  # the citation scrapers' dedupe, while it is kept
        sitio_cur.execute("SELECT user_id, keyword_id, citation_id FROM citation_keyword_notification_sent")
        cit_kw = set(sitio_cur.fetchall())
        sitio_cur.execute("SELECT user_id, citation_id, boletin FROM citation_boletin_notification_sent")
        cit_bol = set(sitio_cur.fetchall())
    return {"palabras": palabras, "seguidos": seguidos, "cit_kw": cit_kw, "cit_bol": cit_bol}


def _solo_antes(k: tuple, m: dict) -> str:
    if k[0] in ("palabra", "plenario") and k[2] not in m["palabras"]:
        return "palabra_borrada"
    if k[0] in ("mencion", "movimiento") and (k[1], k[2]) not in m["seguidos"]:
        return "dejo_de_seguir"
    return "sin_explicar"


def _solo_nuevo(k: tuple, m: dict) -> str:
    if k[0] == "mencion" and m["seguidos"].get((k[1], k[2])) is False:
        return "menciones_unificadas"
    if k[0] == "palabra" and k[3] == "citation" and (k[1], k[2], k[5]) in m["cit_kw"]:
        return "borrada_por_usuario"
    if k[0] == "mencion" and k[3] == "citation" and (k[1], k[5], k[2]) in m["cit_bol"]:
        return "borrada_por_usuario"
    return "sin_explicar"


def comparar(sitio_cur, corrida: Corrida) -> dict:
    antiguas = notificaciones_antiguas(sitio_cur, corrida)
    motivos = _motivos(sitio_cur)
    viejo, nuevo = defaultdict(list), defaultdict(list)
    for f in antiguas:
        viejo[_clave(f)].append(f)
    for _, a in corrida.alertas:
        nuevo[_clave(a.fila)].append(a.fila)

    conteo: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ejemplos: Dict[str, list] = defaultdict(list)

    def anotar(categoria, k, antes=None, ahora=None):
        conteo[k[0]][categoria] += 1
        if len(ejemplos[categoria]) < EJEMPLOS:
            ejemplos[categoria].append({"clave": list(k), "antes": antes, "ahora": ahora})

    for k in sorted(set(viejo) | set(nuevo), key=str):
        v, n = viejo.get(k, []), nuevo.get(k, [])
        if v and n:
            if k[0] in ("plenario", "movimiento"):
                anotar("igual" if len(v) == len(n) else "distinto_numero", k, len(v), len(n))
                continue
            if len(v) > 1:
                anotar("repetida_antes", k, len(v), len(n))
            mensajes_v = {x["message"] for x in v}
            if n[0]["message"] in mensajes_v:
                anotar("igual", k)
            else:
                anotar("mensaje_distinto", k, v[0]["message"][:400], n[0]["message"][:400])
        elif v:
            anotar(_solo_antes(k, motivos), k, v[0]["message"][:300], None)
        else:
            anotar(_solo_nuevo(k, motivos), k, None, n[0]["message"][:300])

    return {
        "items": {k: len(v) for k, v in corrida.items.items()} | {
            f"plenarios_{c}": len(p) for c, p in corrida.plenarios.items()} | (
            {"boletines_movidos": len(corrida.boletines)} if corrida.boletines else {}),
        "notificaciones_antes": len(antiguas),
        "alertas_ahora": len(corrida.alertas),
        "conteo": {t: dict(c) for t, c in conteo.items()},
        "ejemplos": dict(ejemplos),
    }


def backtest(contenido_conn, sitio_conn, desde: datetime, hasta: Optional[datetime], con_comparacion: bool = True) -> dict:
    with contenido_conn.cursor() as c, sitio_conn.cursor() as s:
        listas = leer_listas(s, c)
        corrida = recorrer(c, listas, desde, hasta)
        informe = {"desde": desde.isoformat(), "hasta": hasta.isoformat() if hasta else None,
                   "palabras": len(listas.palabras), "seguidores": len(listas.seguidores)}
        if con_comparacion:
            informe.update(comparar(s, corrida))
        else:
            informe["alertas_ahora"] = len(corrida.alertas)
        return informe


def resumen(informe: dict) -> str:
    lineas = [f"BACKTEST {informe['desde']} → {informe.get('hasta') or 'ahora'}: "
              f"{informe.get('alertas_ahora')} alertas ahora, {informe.get('notificaciones_antes', '?')} antes"]
    for tipo, cats in informe.get("conteo", {}).items():
        lineas.append(f"  {tipo}: " + ", ".join(f"{c}={n}" for c, n in sorted(cats.items())))
    return "\n".join(lineas)


def imprimir(informe: dict) -> None:
    print(resumen(informe))
    print("BACKTEST_JSON " + json.dumps(informe, ensure_ascii=False, default=str))
