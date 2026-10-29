"""Text matching shared by every alert: one copy of what analyse, the citation
scraper, live and the webapp preview each carried.

A keyword matches when its folded form (lowercase, accents stripped) appears in
the folded text: as a whole word for 'exact', anywhere for anything else
('contains'). Excerpts come from the original text, so they keep their accents,
with the hit wrapped in **…** for the email and the web app to bold.

Folding is NFD plus dropping combining marks, as four of the five old copies did.
Live used NFKD, which also turns compatibility characters into letters
(º -> o, ª -> a, ligatures); that difference is accepted, see tests/test_golden.py.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple

Fuente = Tuple[str, str]  # (text, label shown before the excerpt)


def plegar(texto: str) -> str:
    """'SESIÓN' -> 'sesion'. Spanish NFC text keeps its length, so a position in
    the folded text is the same position in the original."""
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn").lower()


def _patron(palabra: str, tipo: str) -> "re.Pattern[str]":
    plegada = re.escape(plegar(palabra))
    return re.compile(r"\b" + plegada + r"\b" if tipo == "exact" else plegada)


def coincide(palabra: str, texto: str, tipo: str) -> bool:
    if not texto or not palabra:
        return False
    if tipo == "exact":
        return bool(_patron(palabra, tipo).search(plegar(str(texto))))
    return plegar(palabra) in plegar(str(texto))


def contar(palabra: str, fuentes: Iterable[Fuente], tipo: str) -> int:
    patron = _patron(palabra, tipo)
    return sum(len(patron.findall(plegar(str(t)))) for t, _ in fuentes if t)


def _fin_de_frase(texto: str, i: int) -> bool:
    """A '.' between digits (20.000) does not end a sentence."""
    c = texto[i]
    if c in "!?\n":
        return True
    if c == ".":
        return not (i > 0 and texto[i - 1].isdigit() and i + 1 < len(texto) and texto[i + 1].isdigit())
    return False


def extracto(palabra: str, texto: str, tipo: str, max_chars: int = 400) -> Optional[str]:
    """The sentence around the first hit, from the original text, hit in **…**."""
    if not palabra or not texto:
        return None
    texto = str(texto).strip()
    if not texto:
        return None
    patron = _patron(palabra, tipo)
    m = patron.search(plegar(texto))
    if not m:
        return None
    pos, largo = m.start(), m.end() - m.start()

    inicio = pos
    while inicio > 0 and not _fin_de_frase(texto, inicio - 1):
        inicio -= 1
    while inicio < pos and texto[inicio] in " \t\r\n":
        inicio += 1
    fin = pos + largo
    while fin < len(texto) and not _fin_de_frase(texto, fin):
        fin += 1
    if fin < len(texto):
        fin += 1  # keep the punctuation
    trozo = texto[inicio:fin].strip()

    # No useful sentence boundaries: a fixed window around the hit
    if len(trozo) < 10 or len(trozo) > max_chars * 2:
        mitad = max_chars // 2
        inicio, fin = max(0, pos - mitad), min(len(texto), pos + mitad)
        trozo = ("…" if inicio > 0 else "") + texto[inicio:fin].strip() + ("…" if fin < len(texto) else "")

    # Hard cap, centred on the hit so it is never cut off
    if len(trozo) > max_chars:
        dentro = patron.search(plegar(trozo))
        kw_ini = dentro.start() if dentro else 0
        kw_fin = dentro.end() if dentro else largo
        resto = max_chars - (kw_fin - kw_ini)
        izq = resto // 2
        der = resto - izq
        v_ini, v_fin = max(0, kw_ini - izq), min(len(trozo), kw_fin + der)
        corte = trozo[v_ini:v_fin]
        if v_ini > 0:
            corte = "…" + (corte.split(" ", 1)[-1] if " " in corte[:30] else corte)
        if v_fin < len(trozo):
            corte = (corte.rsplit(" ", 1)[0] if " " in corte[-30:] else corte).rstrip(".,;:") + "…"
        trozo = corte

    if trozo:
        negrita = patron.search(plegar(trozo))
        if negrita:
            a, b = negrita.start(), negrita.end()
            trozo = trozo[:a] + "**" + trozo[a:b] + "**" + trozo[b:]
    return trozo or None


def contexto(palabra: str, fuentes: Sequence[Fuente], tipo: str) -> Tuple[Optional[str], Optional[str]]:
    """(label, excerpt) from the first source, in priority order, that has a hit."""
    for texto, etiqueta in fuentes:
        if not texto:
            continue
        trozo = extracto(palabra, texto, tipo)
        if trozo:
            return etiqueta, trozo
    return None, None


def variantes_boletin(boletin: str) -> List[str]:
    """'18032-11' -> ['18032-11', '18.032-11']: both ways a boletín is written."""
    boletin = (boletin or "").strip()
    if not boletin:
        return []
    variantes = [boletin]
    m = re.match(r"^(\d{1,3})(\d{3})-(\d+)$", boletin)
    if m:
        variantes.append(f"{m.group(1)}.{m.group(2)}-{m.group(3)}")
    return variantes


def titulo_corto(titulo: str, max_len: int = 140) -> str:
    t = " ".join((titulo or "").split())
    return t if len(t) <= max_len else t[: max_len - 1].rstrip() + "…"
