"""
TRABAJO POR REPOSITORIO — se ejecuta DENTRO del contenedor (uno nuevo por repositorio y corrida).

Recibe un archivo JSON con la "orden de trabajo" que arma el orquestador y hace, en orden:
  M2  Preparación ....... copia el código a un directorio limpio e instala sus dependencias
  M3  Cobertura inicial . corre pytest + coverage y registra la cobertura de partida
      Archivos semilla .. crea un archivo de prueba mínimo por módulo (solo el import)
  M4  Generación ........ ejecuta Qodo Cover (cover-agent) sobre cada módulo
      Extracción ........ lee la base SQLite de cover-agent: TODAS las pruebas, aceptadas y descartadas
      Cobertura final ... corre las pruebas aceptadas y mide la cobertura final
y deja todo en /salida/resultado.json para que el orquestador lo guarde en la base de resultados.

Decisión de diseño importante: hay DOS intérpretes de Python en el contenedor.
  - PYTHON_REPO (/usr/local/bin/python): donde se instalan las dependencias del repositorio
    evaluado y donde corren sus pruebas (pytest, pytest-cov, pytest-timeout).
  - El entorno virtual /opt/qodo: donde vive cover-agent con sus propias dependencias.
Así, si el repositorio evaluado pide versiones de librerías que chocan con las de
cover-agent, ninguno rompe al otro.
"""

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PYTHON_REPO = os.environ.get("PYTHON_REPO", "/usr/local/bin/python")
QODO_BIN = os.environ.get("QODO_BIN", "/opt/qodo/bin/cover-agent")
CARPETA_PRUEBAS = "tests_generados"
# Con DETALLE=1 (python -m pipeline ... --detalle) se muestran también los comandos y la salida cruda
# de Qodo Cover. Sin él, la terminal muestra solo los pasos en lenguaje simple; todo lo demás queda
# igual guardado en los archivos de cada corrida.
DETALLE = os.environ.get("DETALLE") == "1"


def log(msg):
    """Línea técnica: solo se muestra en modo --detalle."""
    if DETALLE:
        print(f"      [detalle {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def paso(msg):
    """Línea en lenguaje simple: se muestra siempre."""
    print(msg, flush=True)


def coma(v, dec=1):
    return "-" if v is None else f"{v:.{dec}f}".replace(".", ",")


def correr(cmd, cwd=None, timeout=None, env=None):
    """Ejecuta un comando de shell y devuelve (codigo, salida combinada)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired as e:
        return -1, (e.stdout or "") + "\n[TIEMPO AGOTADO]"


def cobertura_de_xml(ruta):
    """Lee un reporte Cobertura (XML) y devuelve el porcentaje de líneas cubiertas (0-100)."""
    try:
        raiz = ET.parse(ruta).getroot()
        return round(float(raiz.get("line-rate", 0)) * 100, 2)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# M2. Preparación
# ----------------------------------------------------------------------------
def preparar(orden, repo):
    shutil.copytree(orden.get("directorio_codigo", "/codigo"), repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    pasos = [f"{PYTHON_REPO} -m pip install --no-cache-dir -q --upgrade pip"]
    for archivo in ("requirements.txt", "requirements-dev.txt"):
        if (repo / archivo).exists() and (repo / archivo).read_text().strip():
            pasos.append(f"{PYTHON_REPO} -m pip install --no-cache-dir -q -r {archivo}")
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        pasos.append(f"{PYTHON_REPO} -m pip install --no-cache-dir -q -e .")
    for extra in orden.get("instalacion_extra", []):
        pasos.append(extra)
    for comando in pasos:
        log(f"M2 instalando: {comando}")
        codigo, salida = correr(comando, cwd=repo, timeout=900)
        if codigo != 0:
            return False, f"Falló: {comando}\n{salida[-3000:]}"
    return True, ""


# ----------------------------------------------------------------------------
# M3. Cobertura (inicial y final)
# ----------------------------------------------------------------------------
def comando_pytest(fuente, xml, objetivo="", timeout_prueba=30):
    """
    Comando que corre pytest midiendo cobertura sobre la carpeta `fuente`.
    Pytest devuelve el código 5 cuando NO encuentra ninguna prueba (caso típico de un
    repositorio sin pruebas o de un archivo semilla vacío). Cover-agent aborta ante
    cualquier código distinto de 0, así que el 5 se traduce a 0 (hallazgo del piloto).
    """
    base = (f"{PYTHON_REPO} -m pytest {objetivo} -q -p no:cacheprovider "
            f"--cov={fuente} --cov-report=xml:{xml} --timeout={timeout_prueba}")
    return f"{base}; rc=$?; if [ $rc -eq 5 ]; then exit 0; else exit $rc; fi"


def medir_cobertura(repo, fuente, xml, objetivo="", timeout_prueba=30):
    codigo, salida = correr(comando_pytest(fuente, xml, objetivo, timeout_prueba), cwd=repo, timeout=1800)
    return cobertura_de_xml(xml), codigo, salida


# ----------------------------------------------------------------------------
# Archivos semilla y selección de módulos
# ----------------------------------------------------------------------------
def nombre_modulo(repo, archivo):
    """'src/paquete/mod.py' -> 'paquete.mod' (considera layout src/)."""
    rel = archivo.relative_to(repo).with_suffix("")
    partes = list(rel.parts)
    if partes and partes[0] == "src":
        partes = partes[1:]
    if partes and partes[-1] == "__init__":  # el código del paquete vive en su __init__.py
        partes = partes[:-1]
    return ".".join(partes)


def define_codigo(archivo):
    """True si el archivo define al menos una función o clase."""
    import ast
    try:
        arbol = ast.parse(archivo.read_text(errors="ignore"))
    except SyntaxError:
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in arbol.body)


def elegir_modulos(repo, fuente, maximo):
    """Módulos .py de la carpeta fuente, de mayor a menor tamaño (los más grandes primero)."""
    candidatos = []
    for f in (repo / fuente).rglob("*.py"):
        rel = str(f.relative_to(repo))
        if f.name == "__init__.py" and not define_codigo(f):
            continue  # __init__.py que solo reexporta nombres: no hay lógica que probar
        if re.search(r"(^|/)(tests?|testing)(/|$)|(^|/)test_|_test\.py$|conftest", rel):
            continue
        lineas = sum(1 for l in f.read_text(errors="ignore").splitlines() if l.strip())
        if lineas >= 5 and define_codigo(f):
            candidatos.append((lineas, f))
    candidatos.sort(key=lambda x: (-x[0], str(x[1])))
    return [f for _, f in candidatos[:maximo]]


def crear_semilla(repo, modulo):
    """Archivo de prueba mínimo: SOLO la importación del módulo, ninguna prueba (condición 'sin pruebas')."""
    destino = repo / CARPETA_PRUEBAS / f"test_{modulo.replace('.', '_')}.py"
    destino.parent.mkdir(exist_ok=True)
    if "." in modulo:
        paquete, nombre = modulo.rsplit(".", 1)
        imp = f"from {paquete} import {nombre}"
    else:
        imp = f"import {modulo}"
    destino.write_text(
        "# Archivo semilla creado por el pipeline (condición experimental: sin pruebas previas).\n"
        "# Contiene únicamente la importación del módulo bajo prueba y ninguna prueba.\n"
        f"{imp}  # noqa: F401\n"
    )
    return destino


# ----------------------------------------------------------------------------
# M4. Generación con Qodo Cover y extracción de TODAS las pruebas candidatas
# ----------------------------------------------------------------------------
def extraer_intentos(db):
    """Lee la tabla de intentos de cover-agent (incluye pruebas aceptadas y descartadas)."""
    if not Path(db).exists():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    filas = con.execute(
        "SELECT id, run_time, status, reason, exit_code, stderr, stdout, test_code, imports "
        "FROM unit_test_generation_attempts ORDER BY id").fetchall()
    con.close()
    return [{
        "id_herramienta": f["id"], "momento": str(f["run_time"]), "estado": f["status"],
        "motivo": f["reason"], "codigo_salida": f["exit_code"], "codigo_prueba": f["test_code"],
        "imports": f["imports"],
        "stderr": (f["stderr"] or "")[-4000:], "stdout": (f["stdout"] or "")[-4000:],
    } for f in filas]


# Errores del PROVEEDOR del modelo (no de las pruebas): si aparecen, la corrida no es un dato válido.
# Se buscan nombres de excepción de LiteLLM y estados de la API de Google, no números sueltos
# (un "429" aislado podría ser un número de línea o un porcentaje).
ERRORES_LLM = [
    ("cuota", re.compile(r"RESOURCE_EXHAUSTED|RateLimitError|exceeded your current quota|\"code\": 429")),
    ("saturado", re.compile(r"ServiceUnavailableError|\"status\": \"UNAVAILABLE\"|\"code\": 503|high demand")),
    ("clave", re.compile(r"AuthenticationError|API_KEY_INVALID|PERMISSION_DENIED|API key not valid")),
    ("modelo", re.compile(r"NotFoundError|\"status\": \"NOT_FOUND\"|is no longer available")),
    ("conexion", re.compile(r"APIConnectionError|litellm\.Timeout")),
]


def error_del_llm(texto, codigo):
    """Tipo de error del proveedor del modelo ('cuota', 'saturado', ...) o '' si no hubo.
    Solo cuenta si la herramienta terminó mal: si reintentó y siguió, el error no afectó el dato."""
    if codigo == 0:
        return ""
    for tipo, patron in ERRORES_LLM:
        if patron.search(texto):
            return tipo
    return ""


def tokens_del_log(texto):
    entrada = re.findall(r"Total number of input tokens used for LLM model .*?: (\d+)", texto)
    salida = re.findall(r"Total number of output tokens used for LLM model .*?: (\d+)", texto)
    return (int(entrada[-1]) if entrada else 0), (int(salida[-1]) if salida else 0)


def generar_para_modulo(orden, repo, salida, archivo, modulo):
    h = orden["herramienta"]
    llm = orden["llm"]
    semilla = crear_semilla(repo, modulo)
    carpeta = salida / "modulos" / modulo
    carpeta.mkdir(parents=True, exist_ok=True)
    xml = carpeta / "cobertura.xml"
    db = carpeta / "intentos_cover_agent.db"
    test_cmd = comando_pytest(orden["fuente"], xml, str(semilla.relative_to(repo)), h["timeout_prueba_seg"])
    cmd = [
        QODO_BIN,
        "--source-file-path", str(archivo),
        "--test-file-path", str(semilla),
        "--project-root", str(repo),
        "--code-coverage-report-path", str(xml),
        "--test-command", test_cmd,
        "--test-command-dir", str(repo),
        "--coverage-type", "cobertura",
        "--desired-coverage", str(h["cobertura_deseada"]),
        "--max-iterations", str(h["max_iteraciones"]),
        "--max-run-time-sec", str(h["timeout_prueba_seg"] * 4),
        "--run-tests-multiple-times", str(h["ejecuciones_por_prueba"]),
        "--model", llm["modelo"],
        "--log-db-path", str(db),
        "--report-filepath", str(carpeta / "reporte_cover_agent.html"),
    ]
    if llm.get("api_base"):
        cmd += ["--api-base", llm["api_base"]]
    paso(f"   Archivo {archivo.relative_to(repo)}")
    log(f"M4 generando pruebas para {modulo} con {llm['modelo']}")
    log("comando: " + " ".join(shlex.quote(c) for c in cmd[:5]) + " ...")
    inicio = time.time()
    try:
        r = subprocess.run(cmd, cwd=carpeta, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=h["timeout_modulo_min"] * 60)
        texto, codigo = r.stdout, r.returncode
    except subprocess.TimeoutExpired as e:
        texto, codigo = (e.stdout or "") + "\n[TIEMPO AGOTADO]", -1
    (carpeta / "salida_cover_agent.log").write_text(texto)
    # Resumir lo que hizo la herramienta (la salida completa queda en salida_cover_agent.log)
    ronda = None
    for linea in texto.splitlines():
        if DETALLE and any(k in linea for k in ("Current Coverage", "Iteration", "PASS", "FAIL", "Reached", "Error", "Fatal")):
            print("      | " + linea[-160:], flush=True)
        m = re.search(r"Iteration (\d+) of (\d+)", linea)
        if m:
            ronda = f"{m.group(1)} de {m.group(2)}"
            continue
        # Qodo Cover informa la cobertura AL EMPEZAR cada ronda (antes de generar), y la final en la
        # línea "Reached ..." al terminar. Por eso cada ronda se muestra como "arranca con".
        m = re.search(r"Current Coverage: ([\d.]+)%", linea)
        if m and ronda and not DETALLE:
            paso(f"      ronda {ronda}: arranca con {coma(float(m.group(1)))}% del archivo revisado")
            ronda = None
            continue
        if m and "Reached" in linea and not DETALLE:
            meta = "llegó a la meta" if "above target" in linea else "usó todas las rondas sin llegar a la meta"
            paso(f"      resultado final: {coma(float(m.group(1)))}% del archivo revisado ({meta})")
    tipo_error_llm = error_del_llm(texto, codigo)
    if tipo_error_llm:
        paso(f"      ERROR DE LA IA ({tipo_error_llm}): el modelo no respondió; este archivo no es un dato válido")
    elif codigo != 0 and not DETALLE:
        paso("      aviso: la herramienta terminó con un error (detalle en salida_cover_agent.log)")
    t_in, t_out = tokens_del_log(texto)
    return {
        "modulo": modulo,
        "archivo_fuente": str(archivo.relative_to(repo)),
        "archivo_prueba": str(semilla.relative_to(repo)),
        "codigo_salida_herramienta": codigo,
        "duracion_seg": round(time.time() - inicio, 1),
        "tokens_entrada": t_in,
        "tokens_salida": t_out,
        "intentos": extraer_intentos(db),
        "error_herramienta": texto[-3000:] if codigo != 0 else "",
        "error_llm": tipo_error_llm,
    }


def main():
    orden = json.loads(Path(sys.argv[1]).read_text())
    salida = Path("/salida") if len(sys.argv) < 3 else Path(sys.argv[2])
    repo = Path(orden.get("directorio_trabajo", "/trabajo/repo"))
    salida.mkdir(parents=True, exist_ok=True)
    resultado = {"repo": orden["nombre"], "corrida": orden["corrida"], "estado": "en_curso",
                 "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "modulos": []}
    t0 = time.time()

    def guardar(estado, **extra):
        resultado.update(estado=estado, duracion_seg=round(time.time() - t0, 1), **extra)
        (salida / "resultado.json").write_text(json.dumps(resultado, indent=2, ensure_ascii=False))

    # Versión de las herramientas (trazabilidad, RNF-03)
    resultado["versiones"] = {
        "python_repo": correr(f"{PYTHON_REPO} --version")[1].strip(),
        "cover_agent": correr(f"{Path(QODO_BIN).parent}/pip show cover-agent 2>/dev/null | head -2")[1].strip(),
    }

    # M2
    paso("Paso 2/5  Preparando el proyecto: instalando lo que necesita para funcionar...")
    ok, error = preparar(orden, repo)
    if not ok:
        paso("   No se pudo preparar: falló la instalación de lo que necesita.")
        paso("   Queda registrado y se sigue con el siguiente proyecto.")
        return guardar("no_preparable", error=error)

    # M3 inicial
    cov0, codigo, texto = medir_cobertura(repo, orden["fuente"], salida / "cobertura_inicial.xml",
                                          timeout_prueba=orden["herramienta"]["timeout_prueba_seg"])
    paso(f"Paso 3/5  Midiendo cuánto código revisan sus pruebas actuales: {coma(cov0)}%"
         + ("  (lo esperado: el proyecto no tiene pruebas)" if cov0 is not None and cov0 < 10 else ""))
    log(f"M3 cobertura inicial: {cov0}%  (código pytest {codigo})")
    resultado["cobertura_inicial"] = cov0
    if cov0 is None:
        return guardar("error_cobertura_inicial", error=texto[-3000:])

    # M4 por módulo
    modulos = elegir_modulos(repo, orden["fuente"], orden["herramienta"]["max_modulos"])
    log(f"Módulos seleccionados ({len(modulos)}): " + ", ".join(str(m.relative_to(repo)) for m in modulos))

    # Línea base CON semillas (hallazgo del piloto): importar un módulo ya ejecuta su código de nivel
    # superior (imports, constantes, definiciones de clases y funciones) y eso cuenta como cobertura.
    # Para no atribuirle a la IA lo que aporta el solo import, se mide la cobertura con las semillas
    # de TODOS los módulos elegidos y sin ninguna prueba. El aporte de la IA = final - esta base.
    for archivo in modulos:
        crear_semilla(repo, nombre_modulo(repo, archivo))
    cov_sem, codigo, texto = medir_cobertura(repo, orden["fuente"], salida / "cobertura_semillas.xml",
                                             objetivo=CARPETA_PRUEBAS,
                                             timeout_prueba=orden["herramienta"]["timeout_prueba_seg"])
    resultado["cobertura_semillas"] = cov_sem
    paso(f"          Solo con importar los archivos elegidos (sin ninguna prueba) ya da {coma(cov_sem)}%: "
         "esa es la base para medir lo que aporta la IA")
    log(f"cobertura con semillas: {cov_sem}%  (código pytest {codigo})")

    paso(f"Paso 4/5  Pidiéndole pruebas a la IA a través de Qodo Cover, archivo por archivo "
         f"({len(modulos)} archivo{'s' if len(modulos) != 1 else ''})")

    costo = orden.get("costo", {})
    gastado = 0.0
    errores_llm = []
    for archivo in modulos:
        # RNF-04: límite de costo por repositorio (se controla entre módulo y módulo)
        limite = costo.get("limite_repo_usd")
        if limite and gastado >= limite:
            paso(f"   Se alcanzó el límite de gasto fijado para este proyecto (US$ {limite}): no se procesan más archivos")
            resultado["limite_costo_alcanzado"] = True
            break
        modulo = nombre_modulo(repo, archivo)
        datos = generar_para_modulo(orden, repo, salida, archivo, modulo)
        datos["costo_usd"] = round(datos["tokens_entrada"] / 1e6 * costo.get("entrada_usd_por_millon", 0)
                                   + datos["tokens_salida"] / 1e6 * costo.get("salida_usd_por_millon", 0), 6)
        gastado += datos["costo_usd"]
        resultado["modulos"].append(datos)
        paso(f"      la IA escribió {len(datos['intentos'])} pruebas para este archivo")
        log(f"{modulo}: {len(datos['intentos'])} pruebas candidatas, "
            f"{datos['tokens_entrada']}+{datos['tokens_salida']} tokens, costo estimado {datos['costo_usd']} USD")
        guardar("en_curso")
        if datos["error_llm"]:
            errores_llm.append(f"{datos['archivo_fuente']}: {datos['error_llm']}")
            if datos["error_llm"] in ("cuota", "clave", "modelo"):
                # No se arregla solo reintentando: seguir solo gastaría tiempo (y pedidos) sin producir datos
                paso("   Se detiene este proyecto: con este error la IA no va a responder en los archivos que faltan")
                break

    if errores_llm:
        resultado["error_llm_tipo"] = errores_llm[-1].rsplit(": ", 1)[1]
        paso("Paso 5/5  No se mide la cobertura final: la IA falló y esta corrida hay que repetirla")
        return guardar("error_llm", error="La IA no respondió en: " + "; ".join(errores_llm))

    # Cobertura final con todas las pruebas aceptadas (las que quedaron en los archivos)
    cov1, codigo, texto = medir_cobertura(repo, orden["fuente"], salida / "cobertura_final.xml",
                                          objetivo=CARPETA_PRUEBAS,
                                          timeout_prueba=orden["herramienta"]["timeout_prueba_seg"])
    paso(f"Paso 5/5  Midiendo cuánto código quedó revisado con las pruebas aprobadas: {coma(cov1)}%")
    resultado["cobertura_final"] = cov1
    shutil.copytree(repo / CARPETA_PRUEBAS, salida / CARPETA_PRUEBAS, dirs_exist_ok=True)
    guardar("completo")


if __name__ == "__main__":
    main()
