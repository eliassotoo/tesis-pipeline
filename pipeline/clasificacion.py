"""
M5 (versión PRELIMINAR, hito H4) — Clasificación de cada prueba candidata.

Esta versión clasifica a partir de lo que registró la propia herramienta (cover-agent):
su veredicto, el código de salida de pytest y la salida de error. Es suficiente para el
piloto, pero tiene un límite que hay que declarar: cover-agent anota con el MISMO motivo
("Test failed") a una prueba que no compila, a una que falla siempre y a una inestable.
Por eso en el hito H5 se agrega la re-ejecución independiente de cada prueba (sin red,
N veces, con pytest-timeout), que es la clasificación definitiva del estudio.

Categorías:
  no_compila  -> error de sintaxis, de importación, nombre no definido o fixture inexistente (operacionalización de
                 «compila» para Python, sección 3.4.4 del anteproyecto)
  falla       -> compila, pero no pasa en todas las ejecuciones (falla o es inestable)
  sin_aporte  -> compila y pasa, pero no aumenta la cobertura (la herramienta la descarta)
  aceptada    -> compila, pasa y aumenta la cobertura (la herramienta la conserva)
  error       -> la herramienta no pudo validar la prueba
"""

import ast
import re
import textwrap

PATRONES_NO_COMPILA = re.compile(
    r"SyntaxError|IndentationError|TabError|ImportError|ModuleNotFoundError|NameError|"
    r"errors? during collection|ERROR collecting|fixture '[^']+' not found"
)


def tiene_asercion(codigo):
    """
    ¿La prueba verifica algo? Recorre el árbol sintáctico (módulo ast) buscando:
    sentencias assert, bloques `with pytest.raises(...)`, o llamadas self.assertX(...).
    Una prueba que solo ejecuta código sin comprobar nada suma cobertura, pero no
    cuenta como verificación significativa (ver sección 2.3 del anteproyecto).
    """
    try:
        arbol = ast.parse(textwrap.dedent(codigo or ""))
    except SyntaxError:
        return False
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assert):
            return True
        if isinstance(nodo, ast.Call):
            f = nodo.func
            nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nombre in ("raises", "warns", "fail") or nombre.startswith("assert"):
                return True
    return False


ASERCIONES_DEBILES = {"assertTrue", "assertFalse", "assertIsNotNone", "assertIsNone", "assertIsInstance",
                      "assertNotIsInstance"}


def _es_trivial_o_debil(test):
    """assert True, assert x == x, assert x is not None, assert isinstance(...), assert x (sin comparar)."""
    if isinstance(test, ast.Constant):
        return True
    if isinstance(test, ast.Compare):
        if all(isinstance(n, ast.Constant) for n in [test.left] + test.comparators):
            return True
        if any(ast.dump(test.left) == ast.dump(c) for c in test.comparators):
            return True
        if (len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot))
                and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None):
            return True
        return False
    if isinstance(test, ast.Call) and getattr(test.func, "id", "") in ("isinstance", "hasattr", "callable", "bool"):
        return True
    if isinstance(test, (ast.Name, ast.Attribute, ast.Call)):
        return True  # assert resultado / assert obj.ok: solo comprueba que «algo» sea verdadero
    return False


def verificacion_significativa(codigo):
    """
    ¿La prueba compara el resultado contra un valor esperado o comprueba una excepción? (Tabla 13 del
    anteproyecto: categoría «significativa»). No cuentan: assert True, assert x == x, assert x is not None,
    assert isinstance(...), assert x, assertTrue/assertIsNotNone/assertIsInstance. Es la métrica de la
    hipótesis H2 (se compara entre los grupos A y B).
    """
    try:
        arbol = ast.parse(textwrap.dedent(codigo or ""))
    except SyntaxError:
        return False
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assert) and not _es_trivial_o_debil(nodo.test):
            return True
        if isinstance(nodo, ast.Call):
            f = nodo.func
            nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nombre in ("raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns"):
                return True
            if nombre.startswith("assert") and nombre not in ASERCIONES_DEBILES:
                return True
    return False


def clasificar(intento):
    estado = (intento.get("estado") or "").upper()
    motivo = intento.get("motivo") or ""
    salida = (intento.get("stdout") or "") + "\n" + (intento.get("stderr") or "")
    codigo = intento.get("codigo_salida")

    if estado == "PASS":
        categoria = "aceptada"
    elif motivo.startswith("Coverage did not increase"):
        categoria = "sin_aporte"
    elif motivo.startswith("Test failed"):
        categoria = "no_compila" if (codigo in (2, 4) or PATRONES_NO_COMPILA.search(salida)) else "falla"
    else:
        categoria = "error"

    return {
        "categoria": categoria,
        "compila": categoria in ("aceptada", "sin_aporte", "falla"),
        "pasa": categoria in ("aceptada", "sin_aporte"),
        "aporta": categoria == "aceptada",
        "tiene_asercion": tiene_asercion(intento.get("codigo_prueba")),
        "significativa": verificacion_significativa(intento.get("codigo_prueba")),
    }
