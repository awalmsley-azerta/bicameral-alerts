"""Which alerts a new piece of content produces, for one site's watchlists.

Pure functions: content and watchlists in, alerts out. No database, so the
service, the webapp preview and the tests all run exactly this code.

Each Alerta carries the bicameral_notifications row the producers write today
(`fila`, same columns and values) plus the key that makes it idempotent:
(user_id, regla, objetivo) is unique in alertas_enviadas. The messages are the
producers' messages, word for word.

Boletín alerts are unified (decision of 2026-10-29): following a boletín means
getting its movements and its mentions in sessions and citations, so
`seguidores` is every follower, without the old alert_on_mentions filter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from alertas.texto import coincide, contar, contexto, titulo_corto, variantes_boletin

CAMARA = {"senate": "Senado", "camara": "Cámara de Diputados"}
VENTANA_PLENARIO_S = 5  # live repeat window: on hold, today's value
DIAS_CITACION = 2       # citations dated earlier than this alert nobody


@dataclass(frozen=True)
class Palabra:
    """A bicameral_keywords row. tipo: 'session', 'citation' or 'both'."""
    id: int
    user_id: int
    keyword: str
    match_type: str
    tipo: str

    def aplica(self, fuente: str) -> bool:
        return self.tipo in (fuente, "both")


@dataclass(frozen=True)
class Seguimiento:
    """A user_tracked_boletines row."""
    user_id: int
    boletin: str


@dataclass(frozen=True)
class Sesion:
    id: int
    camara: str  # 'senate' | 'camara'
    commission: Any = ""
    commission_original: Any = ""
    title: Any = ""
    materia: Any = ""
    boletin: Any = ""  # as stored: one number, or a JSON list of them
    date: Any = ""
    time: Any = ""
    transcript_text: Optional[str] = None
    analysis_text: Optional[str] = None


@dataclass(frozen=True)
class Citacion:
    id: int
    camara: str
    commission: Any = ""
    commission_original: Any = ""
    materia: Any = ""
    boletin: Any = ""
    date: Any = None
    time: Any = ""


@dataclass(frozen=True)
class Movimiento:
    """The boletín's trámite count went from `antes` to `despues`."""
    boletin: str
    antes: int
    despues: int


@dataclass(frozen=True)
class Turno:
    """A final transcript turn of a live plenary."""
    plenario_id: int
    camara: str
    texto: str
    inicio_s: float


@dataclass(frozen=True)
class Alerta:
    user_id: int
    regla: str
    objetivo: str
    fila: Dict[str, Any] = field(hash=False)


def _unicas(alertas: Iterable[Alerta]) -> List[Alerta]:
    vistas, salida = set(), []
    for a in alertas:
        clave = (a.user_id, a.regla, a.objetivo)
        if clave not in vistas:
            vistas.add(clave)
            salida.append(a)
    return salida


def _texto_buscable(campos: Sequence[Any]) -> str:
    return " ".join(str(c) for c in campos if c)


def _mensaje_palabra(kw: Palabra, donde: str, camara: str, fuentes) -> str:
    base = f"Palabra clave '{kw.keyword}' encontrada en {donde} — {CAMARA.get(camara, 'Cámara de Diputados')}"
    n = contar(kw.keyword, fuentes, kw.match_type)
    if n > 1:
        base += f" (mencionada {n} {'vez' if n == 1 else 'veces'})"
    etiqueta, trozo = contexto(kw.keyword, fuentes, kw.match_type)
    return f"{base}\n{etiqueta}: «{trozo}»" if etiqueta else base


def _menciones(texto: str, seguidores: Sequence[Seguimiento]) -> Dict[str, str]:
    """For each followed boletín found in the text, the written form that matched."""
    encontrados = {}
    for b in sorted({s.boletin for s in seguidores}):
        for variante in variantes_boletin(b):
            if coincide(variante, texto, "exact"):
                encontrados[b] = variante
                break
    return encontrados


def _alertas_mencion(seguidores, encontrados, titulos, donde, tipo_objetivo, objeto_id, camara, fuentes):
    for s in seguidores:
        variante = encontrados.get(s.boletin)
        if not variante:
            continue
        lineas = [f"Boletín {s.boletin} mencionado en {donde} — {CAMARA.get(camara, 'Cámara de Diputados')}"]
        titulo = (titulos.get(s.boletin) or "").strip()
        if titulo:
            lineas.append(f"Proyecto: «{titulo_corto(titulo)}»")
        etiqueta, trozo = contexto(variante, fuentes, "exact")
        if etiqueta:
            lineas.append(f"{etiqueta}: «{trozo}»")
        yield Alerta(s.user_id, f"mencion:{s.boletin}", f"{tipo_objetivo}:{camara}:{objeto_id}", {
            "user_id": s.user_id, "keyword_id": None, "type": "boletin", "related_id": objeto_id,
            "chamber": camara, "message": "\n".join(lineas), "boletin": s.boletin,
            "origin": "keyword_match", "target_type": tipo_objetivo, "target_id": objeto_id,
            "target_chamber": camara, "target_boletin": s.boletin,
        })


def alertas_sesion(s: Sesion, palabras: Sequence[Palabra], seguidores: Sequence[Seguimiento],
                   titulos: Mapping[str, str]) -> List[Alerta]:
    """Keyword matches and boletín mentions in a newly written session.
    `titulos` maps boletín -> title, for the boletines that may be mentioned."""
    campos = [s.commission, s.commission_original, s.title, s.materia, s.boletin, str(s.date), str(s.time)]
    if s.boletin:
        try:
            lista = json.loads(s.boletin)
            if isinstance(lista, list):
                campos.extend(lista)
        except (json.JSONDecodeError, TypeError):
            pass
    if s.transcript_text:
        campos.append(s.transcript_text)
    if s.analysis_text:
        campos.append(s.analysis_text)
    texto = _texto_buscable(campos)
    fuentes = [(s.title or "", "Título"), (s.materia or "", "Materia"), (s.boletin or "", "Boletín"),
               (s.transcript_text or "", "Transcripción"), (s.analysis_text or "", "Análisis")]
    objetivo = f"session:{s.camara}:{s.id}"

    alertas = []
    for kw in palabras:
        if kw.aplica("session") and coincide(kw.keyword, texto, kw.match_type):
            alertas.append(Alerta(kw.user_id, f"keyword:{kw.id}", objetivo, {
                "user_id": kw.user_id, "keyword_id": kw.id, "type": "session", "related_id": s.id,
                "chamber": s.camara, "message": _mensaje_palabra(kw, "nueva sesión", s.camara, fuentes),
                "origin": "keyword_match", "target_type": "session", "target_id": s.id,
                "target_chamber": s.camara,
            }))
    alertas += _alertas_mencion(seguidores, _menciones(texto, seguidores), titulos, "sesión",
                                "session", s.id, s.camara, fuentes)
    return _unicas(alertas)


def _fecha(valor: Any) -> Optional[date]:
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except (ValueError, TypeError):
        return None


def alertas_citacion(c: Citacion, palabras: Sequence[Palabra], seguidores: Sequence[Seguimiento],
                     titulos: Mapping[str, str], hoy: date) -> List[Alerta]:
    """Keyword matches and boletín mentions in a new or changed citation.
    A citation dated more than DIAS_CITACION days before `hoy` alerts nobody."""
    if c.date:
        f = _fecha(c.date)
        if f is not None and f < hoy - timedelta(days=DIAS_CITACION):
            return []
    texto = _texto_buscable([c.commission, c.commission_original, c.materia, c.boletin, str(c.date), str(c.time)])
    fuentes = [(c.materia or "", "Materia"), (c.commission_original or "", "Comisión"),
               (c.commission or "", "Comisión"), (c.boletin or "", "Boletín")]
    objetivo = f"citation:{c.camara}:{c.id}"

    alertas = []
    for kw in palabras:
        if kw.aplica("citation") and coincide(kw.keyword, texto, kw.match_type):
            alertas.append(Alerta(kw.user_id, f"keyword:{kw.id}", objetivo, {
                "user_id": kw.user_id, "keyword_id": kw.id, "type": "citation", "related_id": c.id,
                "chamber": c.camara, "message": _mensaje_palabra(kw, "nueva citación", c.camara, fuentes),
                "origin": "keyword_match", "target_type": "citation", "target_id": c.id,
                "target_chamber": c.camara,
            }))
    alertas += _alertas_mencion(seguidores, _menciones(texto, seguidores), titulos, "citación",
                                "citation", c.id, c.camara, fuentes)
    return _unicas(alertas)


def alertas_movimiento(m: Movimiento, seguidores: Sequence[Seguimiento], titulo: Optional[str]) -> List[Alerta]:
    """A boletín gained trámites. The first count seen for a boletín (antes == 0)
    only sets the baseline."""
    if m.despues <= m.antes or m.antes <= 0:
        return []
    mensaje = f"Nuevo movimiento en boletín {m.boletin}"
    if titulo:
        mensaje += f"\nProyecto: «{titulo_corto(titulo)}»"
    return _unicas(
        Alerta(s.user_id, f"movimiento:{m.boletin}:{m.despues}", f"boletin:{m.boletin}", {
            "user_id": s.user_id, "keyword_id": None, "type": "boletin", "related_id": 0,
            "chamber": "senate", "message": mensaje, "boletin": m.boletin,
            "origin": "boletin_movement", "target_type": "boletin", "target_boletin": m.boletin,
        })
        for s in seguidores if s.boletin == m.boletin
    )


def alertas_turno(t: Turno, palabras: Sequence[Palabra], ventana_s: float = VENTANA_PLENARIO_S) -> List[Alerta]:
    """Keyword matches in a live plenary turn, at most one per keyword per window."""
    fragmento = t.texto.strip()
    if len(fragmento) > 200:
        fragmento = fragmento[:197] + "…"
    objetivo = f"plenary:{t.camara}:{t.plenario_id}:{int(t.inicio_s // ventana_s)}"
    return _unicas(
        Alerta(kw.user_id, f"keyword:{kw.id}", objetivo, {
            "user_id": kw.user_id, "keyword_id": kw.id, "type": "plenary", "related_id": t.plenario_id,
            "chamber": t.camara, "message": f"«{kw.keyword}» mencionada en sesión de sala: {fragmento}",
            "origin": "keyword_match", "target_id": t.plenario_id, "target_chamber": t.camara,
        })
        for kw in palabras
        if kw.aplica("session") and t.texto and coincide(kw.keyword, t.texto, kw.match_type)
    )
