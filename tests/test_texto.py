"""Text matching on its own."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alertas import coincide, contar, contexto, extracto, plegar, titulo_corto, variantes_boletin


def test_plegar_conserva_el_largo():
    for t in ("Sesión de la Comisión", "PEÑALOLÉN", "ñandú"):
        assert len(plegar(t)) == len(t)
    assert plegar("SESIÓN") == "sesion"


def test_exact_es_palabra_completa_y_contains_no():
    assert coincide("carabinero", "el carabinero declaró", "exact")
    assert not coincide("carabinero", "los carabineros declararon", "exact")
    assert coincide("carabinero", "los carabineros declararon", "contains")
    assert coincide("Reforma", "la REFÓRMA", "contains")
    assert not coincide("", "texto", "exact") and not coincide("x", "", "contains")


def test_tipo_desconocido_es_contains():
    assert coincide("algoritm", "manipulación algorítmica", "otro")


def test_extracto_marca_la_coincidencia_con_tildes_originales():
    t = "Primera frase. El Ministro de Educación expuso el plan. Tercera."
    assert extracto("ministro de educacion", t, "exact") == "El **Ministro de Educación** expuso el plan."


def test_punto_de_miles_no_corta_la_frase():
    t = "Se pidieron 20.000 millones para el fondo. Otra frase."
    assert extracto("fondo", t, "exact") == "Se pidieron 20.000 millones para el **fondo**."


def test_extracto_largo_queda_bajo_el_tope():
    t = "palabra " * 300 + "objetivo " + "palabra " * 300
    e = extracto("objetivo", t, "exact")
    assert "**objetivo**" in e and len(e) <= 400 + 4


def test_contexto_y_conteo_respetan_el_orden_de_fuentes():
    fuentes = [("", "Título"), ("reforma uno", "Materia"), ("reforma dos, reforma tres", "Análisis")]
    assert contexto("reforma", fuentes, "exact") == ("Materia", "**reforma** uno")
    assert contar("reforma", fuentes, "exact") == 3


def test_variantes_boletin():
    assert variantes_boletin("18032-11") == ["18032-11", "18.032-11"]
    assert variantes_boletin("7736-11") == ["7736-11", "7.736-11"]
    assert variantes_boletin(" ") == []


def test_titulo_corto():
    assert titulo_corto("  a   b ") == "a b"
    assert len(titulo_corto("x" * 300)) == 140 and titulo_corto("x" * 300).endswith("…")
