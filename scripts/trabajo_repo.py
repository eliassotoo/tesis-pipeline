"""
TRABAJO POR REPOSITORIO — se ejecuta DENTRO del contenedor (uno nuevo por repositorio y corrida).

Recibe un archivo JSON con la "orden de trabajo" que arma el orquestador y hace, en orden:
  M2  Preparación ....... copia el código a un directorio limpio e instala sus dependencias
                          (grupo B: también las dependencias de prueba que declare el proyecto)
  M3  Cobertura inicial . corre pytest + coverage y registra la cobertura de partida
                          (grupo B: corre la suite preexistente, que tiene que pasar)
      Línea base ........ grupo A: archivo de prueba mínimo por módulo (solo el import) y cobertura con ellos
                          grupo B: la cobertura de la suite preexistente
  M4  Generación ........ ejecuta Qodo Cover (cover-agent) sobre cada módulo
                          (grupo A: extiende el archivo mínimo; grupo B: extiende el archivo de prueba real)
      Extracción ........ lee la base SQLite de cover-agent: TODAS las pruebas, aceptadas y descartadas
      Cobertura final ... corre las pruebas (grupo B: toda la suite) y mide la cobertura final
y deja todo en /salida/resultado.json para que el orquestador lo guarde en la base de resultados.

En el grupo B la herramienta trabaja como en el estudio de Meta: agrega pruebas a una suite que ya existe,
y una prueba solo «aporta» si cubre algo que la suite completa no cubría.

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
import tomllib
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
        extras = extras_de_prueba(repo) if orden.get("grupo") == "B" else []
        objetivo = "." + (f"[{','.join(extras)}]" if extras else "")
        pasos.append(f"{PYTHON_REPO} -m pip install --no-cache-dir -q -e '{objetivo}'")
    if orden.get("grupo") == "B":
        for archivo in ("requirements-test.txt", "requirements_test.txt", "test-requirements.txt",
                        "tests/requirements.txt"):
            if (repo / archivo).exists() and (repo / archivo).read_text().strip():
                pasos.append(f"{PYTHON_REPO} -m pip install --no-cache-dir -q -r {archivo}")
        for grupo_deps in grupos_de_dependencias_de_prueba(repo):
            pasos.append(f"{PYTHON_REPO} -m pip install --no-cache-dir -q --group {grupo_deps}")
    for extra in orden.get("instalacion_extra", []):
        pasos.append(extra)
    for comando in pasos:
        log(f"M2 instalando: {comando}")
        codigo, salida = correr(comando, cwd=repo, timeout=900)
        if codigo != 0:
            return False, f"Falló: {comando}\n{salida[-3000:]}"
    return True, ""


NOMBRES_DEPS_PRUEBA = ("test", "tests", "testing", "dev")


def _pyproject(repo):
    try:
        return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except Exception:
        return {}


def extras_de_prueba(repo):
    """Extras de pyproject.toml con dependencias de prueba ([test], [tests], [testing], [dev])."""
    opcionales = _pyproject(repo).get("project", {}).get("optional-dependencies", {}) or {}
    return [k for k in opcionales if k in NOMBRES_DEPS_PRUEBA]


def grupos_de_dependencias_de_prueba(repo):
    """Grupos de dependencias PEP 735 ([dependency-groups]) con dependencias de prueba."""
    return [k for k in (_pyproject(repo).get("dependency-groups") or {}) if k in NOMBRES_DEPS_PRUEBA]


# ----------------------------------------------------------------------------
# M3. Cobertura (inicial y final)
# ----------------------------------------------------------------------------
def comando_pytest(fuente, xml, objetivo="", timeout_prueba=30, tolerar_vacio=True):
    """
    Comando que corre pytest midiendo cobertura sobre la carpeta `fuente`.
    Pytest devuelve el código 5 cuando NO encuentra ninguna prueba (caso típico de un
    repositorio sin pruebas o de un archivo semilla vacío). Cover-agent aborta ante
    cualquier código distinto de 0, así que en el grupo A el 5 se traduce a 0 (hallazgo del piloto).
    En el grupo B no se traduce: una suite que no encuentra pruebas es un error.
    --cov-fail-under=0 anula un mínimo de cobertura que el proyecto pueda tener configurado
    (por ejemplo «fail_under = 100»): si no, la suite «fallaría» solo por no llegar a ese mínimo.
    """
    base = (f"{PYTHON_REPO} -m pytest {objetivo} -q -p no:cacheprovider "
            f"--cov={fuente} --cov-report=xml:{xml} --cov-fail-under=0 --timeout={timeout_prueba}")
    if not tolerar_vacio:
        return base
    return f"{base}; rc=$?; if [ $rc -eq 5 ]; then exit 0; else exit $rc; fi"


def medir_cobertura(repo, fuente, xml, objetivo="", timeout_prueba=30, tolerar_vacio=True):
    codigo, salida = correr(comando_pytest(fuente, xml, objetivo, timeout_prueba, tolerar_vacio),
                            cwd=repo, timeout=1800)
    return cobertura_de_xml(xml), codigo, salida


def cobertura_por_archivo(xml, repo):
    """{ruta relativa al repo: % de líneas cubiertas} a partir de un reporte Cobertura."""
    try:
        raiz = ET.parse(xml).getroot()
    except Exception:
        return {}
    fuentes = [Path(s.text.strip()) for s in raiz.iter("source") if s.text and s.text.strip()] or [repo]
    salida = {}
    for clase in raiz.iter("class"):
        nombre = clase.get("filename", "")
        for base in fuentes:
            ruta = (base / nombre) if not Path(nombre).is_absolute() else Path(nombre)
            try:
                rel = str(ruta.resolve().relative_to(repo.resolve()))
            except ValueError:
                continue
            salida[rel] = round(float(clase.get("line-rate", 0)) * 100, 2)
            break
    return salida


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


def archivos_de_prueba(repo):
    """Módulos de prueba de pytest (test_*.py o *_test.py). Los datos auxiliares dentro de tests/ no cuentan."""
    return sorted(str(f.relative_to(repo)) for f in repo.rglob("*.py")
                  if (f.name.startswith("test_") or f.name.endswith("_test.py"))
                  and ".venv" not in f.parts and ".git" not in f.parts)


def archivo_de_pruebas_para(repo, archivo, modulo, pruebas, manual=None):
    """
    Grupo B: archivo de prueba preexistente que corresponde a un módulo.
      1. el indicado a mano en el YAML (archivos_prueba), si lo hay;
      2. uno llamado test_<módulo>.py o <módulo>_test.py;
      3. el que más veces importa o menciona el módulo.
    Devuelve None si no hay ninguno: ese módulo no se usa (no hay suite que extender).
    """
    rel = str(archivo.relative_to(repo))
    if manual and manual.get(rel):
        return manual[rel] if (repo / manual[rel]).exists() else None
    corto = archivo.stem if archivo.stem != "__init__" else archivo.parent.name
    por_nombre = [p for p in pruebas if Path(p).name in (f"test_{corto}.py", f"{corto}_test.py")]
    if por_nombre:
        return min(por_nombre, key=len)
    patron = re.compile(rf"^\s*(from|import)\s+[\w.]*\b{re.escape(corto)}\b|^\s*from\s+{re.escape(modulo)}\s+import",
                        re.M)
    puntaje = []
    for p in pruebas:
        n = len(patron.findall((repo / p).read_text(errors="ignore")))
        if n:
            puntaje.append((n, p))
    return max(puntaje)[1] if puntaje else None


def elegir_modulos_b(repo, fuente, maximo, cobertura, manual=None):
    """
    Grupo B: módulos que tienen un archivo de prueba preexistente y no están cubiertos al 100%
    (si ya están cubiertos por completo, ninguna prueba nueva puede aportar). Mismo orden que en el
    grupo A: de mayor a menor tamaño. Devuelve [(archivo_fuente, archivo_de_prueba)].
    """
    pruebas = archivos_de_prueba(repo)
    elegidos = []
    for archivo in elegir_modulos(repo, fuente, 10 ** 6):
        rel = str(archivo.relative_to(repo))
        if cobertura.get(rel, 0.0) >= 100.0:
            continue
        prueba = archivo_de_pruebas_para(repo, archivo, nombre_modulo(repo, archivo), pruebas, manual)
        if prueba:
            elegidos.append((archivo, prueba))
        if len(elegidos) >= maximo:
            break
    return elegidos


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


def consumo_del_uso(ruta):
    """Suma el registro por llamada (tesis_llm) agrupado por tipo de llamada."""
    por_tipo = {}
    if not Path(ruta).exists():
        return por_tipo
    for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
        try:
            f = json.loads(linea)
        except ValueError:
            continue
        t = por_tipo.setdefault(f.get("llamada") or "otra", {"llamadas": 0, "entrada": 0, "salida": 0,
                                                             "razonamiento": 0, "cache": 0})
        t["llamadas"] += 1
        for k in ("entrada", "salida", "razonamiento", "cache"):
            t[k] += int(f.get(k) or 0)
    return por_tipo


def tokens_del_log(texto):
    entrada = re.findall(r"Total number of input tokens used for LLM model .*?: (\d+)", texto)
    salida = re.findall(r"Total number of output tokens used for LLM model .*?: (\d+)", texto)
    return (int(entrada[-1]) if entrada else 0), (int(salida[-1]) if salida else 0)


def generar_para_modulo(orden, repo, salida, archivo, modulo, archivo_prueba=None, duracion_suite=0.0):
    h = orden["herramienta"]
    llm = orden["llm"]
    carpeta = salida / "modulos" / modulo
    carpeta.mkdir(parents=True, exist_ok=True)
    xml = carpeta / "cobertura.xml"
    db = carpeta / "intentos_cover_agent.db"
    uso = carpeta / "uso_llm.jsonl"
    if orden.get("grupo") == "B":
        # Se extiende el archivo de prueba real y se corre la suite COMPLETA: una prueba aporta solo si
        # cubre algo que ninguna prueba preexistente cubría (la condición del estudio de Meta).
        semilla = repo / archivo_prueba
        test_cmd = comando_pytest(orden["fuente"], xml, "", h["timeout_prueba_seg"], tolerar_vacio=False)
    else:
        semilla = crear_semilla(repo, modulo)
        test_cmd = comando_pytest(orden["fuente"], xml, str(semilla.relative_to(repo)), h["timeout_prueba_seg"])
    # Tiempo máximo de cada ejecución de pruebas: en B corre toda la suite, así que se toma en cuenta lo
    # que tardó la suite en el paso 3 para no cortar como «falla» una prueba que en realidad pasaba.
    max_run = max(h["timeout_prueba_seg"] * 4, int(duracion_suite * 3) + 30)
    entorno = dict(os.environ, TESIS_RAZONAMIENTO=h.get("razonamiento") or "",
                   TESIS_ANALISIS_FALLAS=h.get("analisis_fallas") or "local", TESIS_USO_LOG=str(uso))
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
        "--max-run-time-sec", str(max_run),
        "--run-tests-multiple-times", str(h["ejecuciones_por_prueba"]),
        "--model", llm["modelo"],
        "--log-db-path", str(db),
        "--report-filepath", str(carpeta / "reporte_cover_agent.html"),
    ]
    if llm.get("api_base"):
        cmd += ["--api-base", llm["api_base"]]
    paso(f"   Módulo {archivo.relative_to(repo)}")
    log(f"M4 generando pruebas para {modulo} con {llm['modelo']}")
    log("comando: " + " ".join(shlex.quote(c) for c in cmd[:5]) + " ...")
    inicio = time.time()
    try:
        r = subprocess.run(cmd, cwd=carpeta, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=h["timeout_modulo_min"] * 60, env=entorno)
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
            paso(f"      iteración {ronda}: cobertura del módulo al inicio {coma(float(m.group(1)))}%")
            ronda = None
            continue
        if m and "Reached" in linea and not DETALLE:
            meta = "alcanzó el objetivo" if "above target" in linea else "completó las iteraciones sin alcanzar el objetivo"
            paso(f"      cobertura final del módulo: {coma(float(m.group(1)))}% ({meta})")
    tipo_error_llm = error_del_llm(texto, codigo)
    if tipo_error_llm:
        paso(f"      ERROR DE LA IA ({tipo_error_llm}): el modelo no respondió; este archivo no es un dato válido")
    elif codigo != 0 and not DETALLE:
        paso("      aviso: la herramienta terminó con un error (detalle en salida_cover_agent.log)")
    consumo = consumo_del_uso(uso)
    if consumo:
        t_in = sum(t["entrada"] for t in consumo.values())
        t_out = sum(t["salida"] for t in consumo.values())
    else:  # imagen sin los ajustes del estudio: solo el total que informa la herramienta
        t_in, t_out = tokens_del_log(texto)
    return {
        "modulo": modulo,
        "archivo_fuente": str(archivo.relative_to(repo)),
        "archivo_prueba": str(semilla.relative_to(repo)),
        "codigo_salida_herramienta": codigo,
        "duracion_seg": round(time.time() - inicio, 1),
        "tokens_entrada": t_in,
        "tokens_salida": t_out,
        "tokens_razonamiento": sum(t["razonamiento"] for t in consumo.values()),
        "tokens_cache": sum(t["cache"] for t in consumo.values()),
        "consumo_por_tipo": consumo,
        "intentos": extraer_intentos(db),
        "error_herramienta": texto[-3000:] if codigo != 0 else "",
        "error_llm": tipo_error_llm,
    }


def costo_estimado(datos, costo):
    """US$ estimados de un módulo. Los tokens en caché se cobran a su precio si está configurado."""
    precio_in = costo.get("entrada_usd_por_millon", 0) or 0
    precio_cache = costo.get("entrada_cache_usd_por_millon")
    precio_cache = precio_in if precio_cache is None else precio_cache
    cache = min(datos.get("tokens_cache", 0), datos["tokens_entrada"])
    return round(((datos["tokens_entrada"] - cache) * precio_in + cache * precio_cache
                  + datos["tokens_salida"] * (costo.get("salida_usd_por_millon", 0) or 0)) / 1e6, 6)


def ajustes_instalados():
    """True si el entorno de la herramienta tiene los ajustes del estudio (docker/parche_qodo.py)."""
    py = Path(QODO_BIN).parent / "python"
    codigo, _ = correr(f"{shlex.quote(str(py))} -c 'import tesis_llm'")
    return codigo == 0


def main():
    orden = json.loads(Path(sys.argv[1]).read_text())
    salida = Path("/salida") if len(sys.argv) < 3 else Path(sys.argv[2])
    repo = Path(orden.get("directorio_trabajo", "/trabajo/repo"))
    salida.mkdir(parents=True, exist_ok=True)
    grupo = orden.get("grupo", "A")
    crit = orden.get("criterios", {})
    h = orden["herramienta"]
    resultado = {"repo": orden["nombre"], "corrida": orden["corrida"], "grupo": grupo, "estado": "en_curso",
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
    if not ajustes_instalados():
        paso("   La herramienta no tiene los ajustes del estudio: reconstruí la imagen con "
             "'python -m pipeline construir-imagen'.")
        return guardar("error_entorno", error="Falta tesis_llm en el entorno de Qodo Cover (docker/parche_qodo.py)")

    # M2
    paso("Paso 2/5  Preparación: instalación de dependencias en el entorno aislado...")
    ok, error = preparar(orden, repo)
    if not ok:
        paso("   No fue posible instalar sus dependencias.")
        paso("   Se registra el descarte y se continúa con el proyecto siguiente.")
        return guardar("no_preparable", error=error)

    # M3 inicial
    t_suite = time.time()
    cov0, codigo, texto = medir_cobertura(repo, orden["fuente"], salida / "cobertura_inicial.xml",
                                          timeout_prueba=h["timeout_prueba_seg"], tolerar_vacio=(grupo == "A"))
    duracion_suite = time.time() - t_suite
    resultado["cobertura_inicial"] = cov0
    log(f"M3 cobertura inicial: {cov0}%  (código pytest {codigo}, {duracion_suite:.1f} s)")
    if grupo == "B":
        paso(f"Paso 3/5  Cobertura inicial de la suite preexistente: {coma(cov0)}%"
             + (f"  (duración de la suite: {coma(duracion_suite, 1)} s)" if cov0 is not None else ""))
        if codigo != 0:
            paso("   La suite preexistente no pasa en el entorno aislado: el proyecto no es apto para el grupo B.")
            return guardar("suite_falla", error=texto[-3000:])
    else:
        paso(f"Paso 3/5  Cobertura inicial: {coma(cov0)}%"
             + ("  (esperada: el proyecto no tiene pruebas)" if cov0 is not None and cov0 < 10 else ""))
    if cov0 is None:
        return guardar("error_cobertura_inicial", error=texto[-3000:])
    if grupo == "A" and cov0 > crit.get("cobertura_a_max", 10):
        paso(f"   Cobertura inicial mayor al {crit.get('cobertura_a_max', 10)}% exigido al grupo A: queda afuera.")
        return guardar("fuera_de_rango", error=f"Cobertura inicial {cov0}% (grupo A: máximo {crit.get('cobertura_a_max', 10)}%)")
    if grupo == "B" and not (crit.get("cobertura_b_min", 20) <= cov0 <= crit.get("cobertura_b_max", 80)):
        paso(f"   Cobertura inicial fuera del rango del grupo B ({crit.get('cobertura_b_min', 20)}% a "
             f"{crit.get('cobertura_b_max', 80)}%): queda afuera.")
        return guardar("fuera_de_rango", error=f"Cobertura inicial {cov0}% (grupo B: entre "
                                               f"{crit.get('cobertura_b_min', 20)}% y {crit.get('cobertura_b_max', 80)}%)")

    # Módulos y línea base
    if grupo == "B":
        pares = elegir_modulos_b(repo, orden["fuente"], h["max_modulos"],
                                 cobertura_por_archivo(salida / "cobertura_inicial.xml", repo),
                                 orden.get("archivos_prueba"))
        if not pares:
            paso("   Ningún módulo tiene un archivo de pruebas preexistente y líneas sin cubrir.")
            return guardar("sin_modulos", error="Ningún módulo con archivo de prueba preexistente y cobertura < 100%")
        # Línea base del grupo B: lo que ya cubre la suite preexistente
        resultado["cobertura_base"] = cov0
        paso("          Línea base: una prueba generada solo se acepta si cubre líneas que la suite "
             f"preexistente ({len(archivos_de_prueba(repo))} archivos de prueba) no cubre")
    else:
        pares = [(a, None) for a in elegir_modulos(repo, orden["fuente"], h["max_modulos"])]
        # Línea base CON semillas (hallazgo del piloto): importar un módulo ya ejecuta su código de nivel
        # superior (imports, constantes, definiciones de clases y funciones) y eso cuenta como cobertura.
        # Para no atribuirle a la IA lo que aporta el solo import, se mide la cobertura con las semillas
        # de TODOS los módulos elegidos y sin ninguna prueba. El aporte de la IA = final - esta base.
        for archivo, _ in pares:
            crear_semilla(repo, nombre_modulo(repo, archivo))
        cov_sem, codigo, texto = medir_cobertura(repo, orden["fuente"], salida / "cobertura_semillas.xml",
                                                 objetivo=CARPETA_PRUEBAS, timeout_prueba=h["timeout_prueba_seg"])
        resultado["cobertura_semillas"] = cov_sem
        resultado["cobertura_base"] = cov_sem
        paso(f"          Cobertura atribuible a la importación de los módulos: {coma(cov_sem)}% "
             "(línea base, no se atribuye al modelo)")
        log(f"cobertura con semillas: {cov_sem}%  (código pytest {codigo})")
    log("Módulos seleccionados: " + ", ".join(f"{a.relative_to(repo)}" + (f" -> {t}" if t else "") for a, t in pares))

    paso(f"Paso 4/5  Generación de pruebas con Qodo Cover y el modelo de lenguaje "
         f"({len(pares)} módulo{'s' if len(pares) != 1 else ''})")

    costo = orden.get("costo", {})
    gastado = 0.0
    errores_llm = []
    for archivo, archivo_prueba in pares:
        # RNF-04: límite de costo por repositorio (se controla entre módulo y módulo)
        limite = costo.get("limite_repo_usd")
        if limite and gastado >= limite:
            paso(f"   Se alcanzó el límite de gasto fijado para este proyecto (US$ {limite}): no se procesan más archivos")
            resultado["limite_costo_alcanzado"] = True
            break
        modulo = nombre_modulo(repo, archivo)
        if archivo_prueba:
            paso(f"   (amplía su archivo de pruebas {archivo_prueba})")
        datos = generar_para_modulo(orden, repo, salida, archivo, modulo, archivo_prueba, duracion_suite)
        datos["costo_usd"] = costo_estimado(datos, costo)
        gastado += datos["costo_usd"]
        resultado["modulos"].append(datos)
        paso(f"      pruebas generadas: {len(datos['intentos'])} "
             f"({datos['tokens_entrada'] + datos['tokens_salida']:,} tokens, US$ {coma(datos['costo_usd'], 3)})".replace(",", "."))
        log(f"{modulo}: {len(datos['intentos'])} pruebas candidatas, {datos['tokens_entrada']}+{datos['tokens_salida']} "
            f"tokens (razonamiento {datos.get('tokens_razonamiento', 0)}), costo estimado {datos['costo_usd']} USD")
        guardar("en_curso")
        if datos["error_llm"]:
            errores_llm.append(f"{datos['archivo_fuente']}: {datos['error_llm']}")
            if datos["error_llm"] in ("cuota", "clave", "modelo"):
                # No se arregla solo reintentando: seguir solo gastaría tiempo (y pedidos) sin producir datos
                paso("   Se detiene este proyecto: con este error la IA no va a responder en los archivos que faltan")
                break

    if errores_llm:
        resultado["error_llm_tipo"] = errores_llm[-1].rsplit(": ", 1)[1]
        paso("Paso 5/5  No se mide la cobertura final: el modelo no respondió y la corrida debe repetirse")
        return guardar("error_llm", error="La IA no respondió en: " + "; ".join(errores_llm))

    # Cobertura final: grupo A, las pruebas aprobadas; grupo B, toda la suite (preexistente + aprobadas)
    cov1, codigo, texto = medir_cobertura(repo, orden["fuente"], salida / "cobertura_final.xml",
                                          objetivo=CARPETA_PRUEBAS if grupo == "A" else "",
                                          timeout_prueba=h["timeout_prueba_seg"], tolerar_vacio=(grupo == "A"))
    paso(f"Paso 5/5  Cobertura final con las pruebas aceptadas: {coma(cov1)}%")
    resultado["cobertura_final"] = cov1
    if grupo == "A":
        shutil.copytree(repo / CARPETA_PRUEBAS, salida / CARPETA_PRUEBAS, dirs_exist_ok=True)
    else:  # copia de los archivos de prueba extendidos, como evidencia
        for _, archivo_prueba in pares:
            destino = salida / "pruebas_extendidas" / archivo_prueba
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(repo / archivo_prueba, destino)
    guardar("completo")


if __name__ == "__main__":
    main()
