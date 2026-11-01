"""
Ajustes del estudio sobre Qodo Cover (cover-agent). Se instala junto a la herramienta, en su
mismo entorno de Python, con docker/parche_qodo.py, que además agrega dos llamadas a este módulo
en el código de la herramienta. Nada de esto cambia los criterios de filtrado (compila, pasa N veces,
aumenta la cobertura): solo controla cuánto se le pide al modelo y registra lo que se gastó.

Se configura con variables de entorno, que fija el pipeline a partir del YAML del experimento
(así el valor usado queda registrado en cada ejecución, RNF-05):

  TESIS_RAZONAMIENTO     Nivel de razonamiento interno del modelo («thinking»): minimal | low | medium |
                         high | disable. Vacío = el valor por defecto del modelo (comportamiento original).
                         En Gemini, los tokens de razonamiento se cobran como tokens de salida.
  TESIS_ANALISIS_FALLAS  ia | local. Qodo Cover, por cada prueba que falla, le pide al modelo que explique
                         el error (una llamada con el archivo fuente y el de pruebas completos). Con «local»
                         la explicación se arma con las líneas de error de pytest, sin llamar al modelo.
                         Sin la variable: «ia» (comportamiento original).
  TESIS_USO_LOG          Archivo JSONL donde se anota cada llamada al modelo: para qué fue, tokens de
                         entrada, de salida, de razonamiento y en caché.
"""

import json
import os
import re
import time

NIVELES_RAZONAMIENTO = ("minimal", "low", "medium", "high", "disable")


def parametros_extra(modelo=None):
    """Parámetros que se agregan a cada llamada a litellm.completion()."""
    extra = {}
    nivel = os.environ.get("TESIS_RAZONAMIENTO", "").strip().lower()
    if nivel:
        if nivel not in NIVELES_RAZONAMIENTO:
            raise ValueError(f"TESIS_RAZONAMIENTO inválido: {nivel!r} (válidos: {', '.join(NIVELES_RAZONAMIENTO)})")
        extra["reasoning_effort"] = nivel
    return extra


def analisis_local():
    return os.environ.get("TESIS_ANALISIS_FALLAS", "ia").strip().lower() == "local"


_LINEA_ERROR = re.compile(r"^E\s|Error|Exception|assert|FAILED|Traceback|^>\s")


def resumen_local_de_falla(stdout, stderr, limite=1500):
    """
    Resumen de por qué falló una prueba, armado con la salida de pytest (sin llamar al modelo).
    Se queda con las líneas que pytest marca como error («E   ...», FAILED, excepciones); si no hay,
    con las últimas líneas de la salida.
    """
    texto = ((stdout or "") + "\n" + (stderr or "")).strip()
    lineas = [l.rstrip() for l in texto.splitlines() if l.strip()]
    utiles = [l for l in lineas if _LINEA_ERROR.search(l)]
    elegidas = (utiles or lineas)[-25:]
    resumen = "\n".join(elegidas)
    if len(resumen) > limite:
        resumen = "..." + resumen[-limite:]
    return resumen


def _valor(obj, *camino):
    for clave in camino:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(clave)
        else:
            obj = getattr(obj, clave, None)
    return obj


def registrar_uso(llamada, modelo, usage, tokens_entrada, tokens_salida):
    """Anota una llamada al modelo en TESIS_USO_LOG. Nunca interrumpe a la herramienta."""
    ruta = os.environ.get("TESIS_USO_LOG")
    if not ruta:
        return
    try:
        razonamiento = _valor(usage, "completion_tokens_details", "reasoning_tokens")
        if razonamiento is None:
            razonamiento = _valor(usage, "reasoning_tokens")
        cache = _valor(usage, "prompt_tokens_details", "cached_tokens")
        fila = {
            "momento": time.strftime("%Y-%m-%d %H:%M:%S"),
            "llamada": llamada,
            "modelo": modelo,
            "razonamiento_pedido": os.environ.get("TESIS_RAZONAMIENTO", ""),
            "entrada": int(tokens_entrada or 0),
            "salida": int(tokens_salida or 0),
            "razonamiento": int(razonamiento or 0),
            "cache": int(cache or 0),
        }
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except Exception:
        pass
