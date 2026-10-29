"""Inputs shared by the golden recorder and the tests: four real v3 reports turned
into sessions, plus citations, boletín movements and plenary turns written to
reach every branch of the old producers (excerpt fallbacks, the 2-day citation
cut-off, the first-count seed, both written forms of a boletín, accents, ...).

Plain data only (no alertas imports), so scripts/grabar_golden.py can feed the
same cases to the old code.
"""
import json
from datetime import date
from pathlib import Path

INFORMES = Path(__file__).parent / "fixtures" / "informes"
HOY = date(2026, 10, 29)

# bicameral_keywords rows: id, user_id, keyword, match_type, type
PALABRAS = [
    (1, 1, "reforma", "contains", "session"),
    (2, 2, "mercado de capitales", "exact", "both"),
    (3, 3, "Ministro del Interior", "exact", "session"),
    (4, 4, "ADN", "exact", "both"),
    (5, 1, "subrogación", "exact", "citation"),
    (6, 2, "algoritm", "contains", "session"),
    (7, 3, "palabra que no aparece", "exact", "both"),
    (8, 4, "carabinero", "exact", "session"),
    (9, 1, "sesion", "contains", "both"),
    (10, 2, "Reforma", "contains", "citation"),
    (11, 3, "20.000", "exact", "both"),
    (12, 4, "nº 5", "contains", "session"),
    (13, 2, "urgencia", "exact", "both"),
]

# user_tracked_boletines rows: user_id, boletin (every follower: alerts are unified)
SEGUIDORES = [
    (1, "15222-06"), (2, "17662-07"), (2, "18585-25"), (3, "18643-05"),
    (4, "99999-99"), (1, "17662-07"), (3, "15222-06"),
]
TITULOS = {
    "15222-06": "Modifica las leyes orgánicas constitucionales de Municipalidades y de Gobierno y "
                "Administración Regional, para regular la subrogación de autoridades comunales y regionales "
                "que hagan uso de permisos prenatal, postnatal o postnatal parental",
    "17662-07": "Aumenta sanciones aplicables al delito de atentado contra la autoridad",
    "18643-05": "",
}


def _texto_informe(inf: dict) -> str:
    partes = list(inf["resumen"]) if isinstance(inf["resumen"], list) else [inf["resumen"]]
    for b in inf["bloques"]:
        partes.append(b.get("bajada") or b.get("etiqueta") or "")
        for iv in b["intervenciones"]:
            partes.append(f"{iv['sujeto']}: " + " ".join(iv.get("parrafos") or []))
    partes += inf.get("acuerdos") or []
    return "\n".join(p for p in partes if p)


def _informe(run_id: str) -> dict:
    return json.loads((INFORMES / f"{run_id}.json").read_text(encoding="utf-8"))


def sesiones() -> list:
    """Session rows as analyse passes them to _check_session_keywords."""
    filas = []
    boletines = {"012494a858f40b19": "18643-05",
                 "b6507d8bb7b8075b": "15222-06",
                 "e79591536672bb4a": json.dumps(["17662-07", "18585-25"]),
                 "ed17ba590cdae725": None}
    for i, run_id in enumerate(sorted(boletines), start=1):
        inf = _informe(run_id)
        texto = _texto_informe(inf)
        temas = inf["temas"] if isinstance(inf["temas"], list) else [inf["temas"]]
        filas.append({
            "id": 100 + i, "camara": "camara" if inf["camara"] == "camara" else "senate",
            "commission": inf["comision"].lower(), "commission_original": inf["comision"],
            "title": inf["comision"], "materia": "; ".join(temas), "boletin": boletines[run_id],
            "date": inf["fecha"] if i != 4 else None, "time": "15:00",
            # one session with a transcript, the others with the analysis only
            "transcript_text": texto if i == 2 else None,
            "analysis_text": texto,
        })
    # No sentence boundaries, longer than the excerpt cap: the fixed-window fallback
    larga = " ".join(["la comisión discutió el financiamiento"] * 40) + " y la reforma tributaria " + \
        " ".join(["sin conclusiones"] * 60)
    filas.append({"id": 201, "camara": "senate", "commission": "hacienda", "commission_original": "Comisión de Hacienda",
                  "title": "Sesión ordinaria", "materia": "", "boletin": "", "date": "2026-10-28", "time": "",
                  "transcript_text": None, "analysis_text": larga})
    # Thousands separators are not sentence ends; the boletín written with a dot
    filas.append({"id": 202, "camara": "camara", "commission": "vivienda", "commission_original": "Comisión de Vivienda",
                  "title": "Subsidios", "materia": "Boletín N° 15.222-06 y otros",
                  "boletin": "", "date": "2026-10-27", "time": "10:00", "transcript_text": None,
                  "analysis_text": "Se informó que 20.000 familias postularon. El Ministro del Interior no asistió. "
                                   "Sesión, sesion y SESIÓN aparecen aquí; la urgencia es simple."})
    return filas


def citaciones() -> list:
    """Citation rows as the scrapers pass them to _check_citation_keywords."""
    temas = _informe("e79591536672bb4a")["temas"]
    return [
        {"id": 301, "camara": "senate", "commission": "seguridad-publica", "commission_original": "Comisión de Seguridad Pública",
         "materia": temas[0], "boletin": "17662-07", "date": "2026-10-29", "time": "15:00"},
        {"id": 302, "camara": "senate", "commission": "seguridad-publica", "commission_original": "Comisión de Seguridad Pública",
         "materia": temas[1], "boletin": "18585-25", "date": "2026-10-28", "time": "15:00"},
        # older than two days: nobody is alerted
        {"id": 303, "camara": "camara", "commission": "hacienda", "commission_original": "Comisión de Hacienda",
         "materia": "Reforma de Mercado de Capitales, boletín 18643-05", "boletin": "18643-05",
         "date": "2026-10-20", "time": "10:00"},
        {"id": 304, "camara": "camara", "commission": "gobierno", "commission_original": "Comisión de Gobierno Interior",
         "materia": "Subrogación de alcaldes (Boletín N° 15.222-06) y reforma municipal", "boletin": "",
         "date": None, "time": ""},
        {"id": 305, "camara": "camara", "commission": "hacienda", "commission_original": "Comisión de Hacienda",
         "materia": "Proyecto que crea un registro de ADN; urgencia suma", "boletin": "",
         "date": "2026-10-27", "time": "12:00"},
    ]


# (boletin, trámites before, trámites after)
MOVIMIENTOS = [
    ("15222-06", 3, 4),
    ("17662-07", 0, 2),   # first count seen: baseline, no alert
    ("18585-25", 5, 5),   # no change
    ("17662-07", 2, 3),
    ("18643-05", 7, 9),   # empty title
    ("99999-99", 1, 2),
]


def turnos() -> list:
    """Final plenary turns: chamber, plenary id, text, start second."""
    inf = _informe("ed17ba590cdae725")
    textos = [" ".join(iv.get("parrafos") or []) for b in inf["bloques"] for iv in b["intervenciones"]][:6]
    salida = [("camara", 55, t, 12.0 * i) for i, t in enumerate(textos)]
    salida += [
        ("senate", 77, "El senador pidió votar la reforma; nº 5 de la tabla.", 3.0),
        ("senate", 77, "Insistió en la reforma y en el Nº 5.", 4.0),     # same 5 s window as the previous turn
        ("senate", 77, "Volvió sobre la reforma " + "muy " * 80 + "larga.", 9.0),
        ("senate", 77, "", 20.0),
        ("senate", 77, "El ministro del interior respondió. " * 3, 31.5),
    ]
    return salida


def xml_tramites(n: int) -> str:
    return "<proyecto><tramitacion>" + "<tramite><etapa>x</etapa></tramite>" * n + "</tramitacion></proyecto>"
