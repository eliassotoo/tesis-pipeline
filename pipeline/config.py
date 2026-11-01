"""Carga y valida el archivo de configuración YAML de un experimento (RNF-01: configuración versionada)."""

import copy
import hashlib
import json
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent

# Modelo de lenguaje por defecto: Gemini, que tiene plan gratuito (clave de Google AI Studio).
# Si Google lo renombra, `python -m pipeline modelos` lista los nombres vigentes para tu clave.
MODELO_POR_DEFECTO = "gemini/gemini-3.6-flash"

# Imagen del ejecutor. La etiqueta cambia cuando cambia el Dockerfile, para no usar una imagen vieja.
IMAGEN_DOCKER = "tesis-pipeline-runner:h4-grupos"

# Grupos del diseño experimental (sección 2.3 del anteproyecto)
GRUPOS = {"A": "sin pruebas preexistentes", "B": "con suite de pruebas preexistente"}
NIVELES_RAZONAMIENTO = ("", "minimal", "low", "medium", "high", "disable")

# Proveedor (lo que va antes de la barra en el nombre del modelo) -> variable de entorno con la clave.
CLAVES_POR_PROVEEDOR = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
CLAVES_API = list(CLAVES_POR_PROVEEDOR.values())

# Valores por defecto: cualquier clave que falte en el YAML toma estos valores.
POR_DEFECTO = {
    "experimento": {"nombre": "sin_nombre", "corridas_por_repo": 1, "carpeta_resultados": "resultados"},
    "llm": {"modelo": MODELO_POR_DEFECTO, "api_base": "", "entrada_usd_por_millon": 0.0, "salida_usd_por_millon": 0.0},
    "herramienta": {
        "qodo_cover_commit": "adcf55baba40e8fbf8a84e68b70d08bef1e2b9c9",
        "max_iteraciones": 3,
        "cobertura_deseada": 100,
        "ejecuciones_por_prueba": 5,
        "timeout_prueba_seg": 30,
        "max_modulos": 5,
        "timeout_modulo_min": 20,
        # Ajustes de consumo (docker/tesis_llm.py). Se registran con cada ejecución (RNF-05).
        "razonamiento": "",          # nivel de «thinking» del modelo: minimal|low|medium|high|disable; "" = el del modelo
        "analisis_fallas": "local",  # local = explicar la falla con la salida de pytest; ia = pedírsela al modelo
    },
    "limites": {"limite_costo_repo_usd": 1.0, "tiempo_max_repo_min": 60, "cpus": 2, "memoria": "4g"},
    "criterios": {"lineas_min": 500, "lineas_max": 5000, "actividad_min_dias": 365,
                  # Cobertura inicial exigida a cada grupo (sección 2.3 del anteproyecto; umbrales tentativos)
                  "cobertura_a_max": 10, "cobertura_b_min": 20, "cobertura_b_max": 80},
    "docker": {"imagen": IMAGEN_DOCKER},
    "repositorios": [],
}


def _fusionar(base, extra):
    salida = copy.deepcopy(base)
    for clave, valor in (extra or {}).items():
        if isinstance(valor, dict) and isinstance(salida.get(clave), dict):
            salida[clave] = _fusionar(salida[clave], valor)
        else:
            salida[clave] = valor
    return salida


def clave_api_requerida(modelo):
    """Nombre de la variable de entorno con la clave que necesita ese modelo ('gemini/...' -> GEMINI_API_KEY)."""
    proveedor = (modelo or "").split("/", 1)[0].lower()
    return CLAVES_POR_PROVEEDOR.get(proveedor)


def cargar(ruta, modelo=None, api_base=None):
    ruta = Path(ruta)
    if not ruta.exists():
        raise SystemExit(f"No existe el archivo de configuración: {ruta}")
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    cfg = _fusionar(POR_DEFECTO, datos)
    if modelo:  # permite cambiar el modelo desde la línea de comandos sin tocar el YAML
        cfg["llm"]["modelo"] = modelo
        cfg["llm"]["api_base"] = api_base or ""
    if not cfg["repositorios"]:
        raise SystemExit("La configuración no tiene repositorios en la lista 'repositorios'.")
    for r in cfg["repositorios"]:
        for clave in ("nombre", "url", "commit", "fuente"):
            if not r.get(clave):
                raise SystemExit(f"Repositorio sin '{clave}': {r}\n"
                                 "Cada repositorio necesita: nombre, url (de GitHub), commit (fijo, RNF-01) y fuente "
                                 "(carpeta del código dentro del repositorio).")
        r["grupo"] = str(r.get("grupo", "A")).strip().upper()
        if r["grupo"] not in GRUPOS:
            raise SystemExit(f"El repositorio {r['nombre']} tiene grupo '{r['grupo']}': tiene que ser A "
                             f"({GRUPOS['A']}) o B ({GRUPOS['B']}).")
        if "github.com" not in r["url"]:
            raise SystemExit(f"El repositorio {r['nombre']} debe ser un repositorio público de GitHub "
                             f"(url con github.com), no: {r['url']}")
    nivel = str(cfg["herramienta"].get("razonamiento") or "").strip().lower()
    if nivel not in NIVELES_RAZONAMIENTO:
        raise SystemExit(f"herramienta.razonamiento = '{nivel}' no es válido. Opciones: "
                         + ", ".join(repr(n) for n in NIVELES_RAZONAMIENTO))
    cfg["herramienta"]["razonamiento"] = nivel
    if cfg["herramienta"].get("analisis_fallas") not in ("local", "ia"):
        raise SystemExit("herramienta.analisis_fallas tiene que ser 'local' o 'ia'.")
    cfg["_archivo"] = str(ruta)
    cfg["_huella"] = hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return cfg
