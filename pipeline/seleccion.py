"""
M1. Selección de repositorios.

Dos funciones:
  buscar()     -> consulta la API de GitHub y arma una lista de CANDIDATOS (CSV) para revisión humana.
  verificar()  -> para cada repositorio de la configuración: lo descarga en un commit fijo y
                  verifica uno por uno los criterios de inclusión, registrando el detalle (RF-01, RF-02).
                  Todo descarte queda registrado con su motivo (conjunto auditable).
"""

import csv
import datetime as dt
import os
import re
import shutil
import subprocess
from pathlib import Path

PATRON_PRUEBAS = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")


# ----------------------------------------------------------------------------
# Descarga del código (commit fijo)
# ----------------------------------------------------------------------------
def preparar_codigo(repo, carpeta_cache):
    """Descarga el repositorio público de GitHub en el commit indicado y devuelve (carpeta, commit)."""
    destino = Path(carpeta_cache) / repo["nombre"]
    if destino.exists():
        actual = subprocess.run(["git", "-C", str(destino), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        if actual == repo["commit"]:
            return destino, repo["commit"]
        shutil.rmtree(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    print(f"   descargando {repo['url']} ...")
    _git(["clone", "--quiet", "--filter=blob:none", "--no-checkout", repo["url"], str(destino)])
    _git(["-C", str(destino), "checkout", "--quiet", repo["commit"]])
    return destino, repo["commit"]


def _git(args):
    r = subprocess.run(["git"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} falló: {r.stderr.strip()}")
    return r.stdout


# ----------------------------------------------------------------------------
# Criterios
# ----------------------------------------------------------------------------
def contar_lineas(carpeta):
    """Líneas de código (sin comentarios ni líneas vacías) de los .py, medido con pygount."""
    from pygount import SourceAnalysis
    total = 0
    for f in Path(carpeta).rglob("*.py"):
        if PATRON_PRUEBAS.search(str(f.relative_to(carpeta))) or ".venv" in f.parts:
            continue
        # encoding utf-8 explícito: con 'automatic' pygount cae a cp1252 y falla con tildes/emojis
        total += SourceAnalysis.from_file(str(f), "tesis", encoding="utf-8").code_count
    return total


def detectar_pruebas(raiz):
    """Busca señales de pruebas existentes: archivos/carpetas de test y configuración de pytest."""
    hallazgos = []
    for f in Path(raiz).rglob("*"):
        if ".git" in f.parts or ".venv" in f.parts:
            continue
        rel = str(f.relative_to(raiz))
        if f.is_file() and f.suffix == ".py" and PATRON_PRUEBAS.search(rel):
            hallazgos.append(rel)
    for nombre, marca in (("pytest.ini", ""), ("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"),
                          ("tox.ini", "[pytest]")):
        archivo = Path(raiz) / nombre
        if archivo.exists() and marca in archivo.read_text(errors="ignore"):
            hallazgos.append(f"{nombre} (configuración de pytest)")
    return hallazgos


def dependencias_declaradas(raiz):
    """
    Devuelve (cumple, detalle). Cumple si hay un archivo de dependencias, o si el código solo
    importa la biblioteca estándar y sus propios módulos (entonces no hay nada que instalar).
    """
    archivos = [n for n in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile")
                if (Path(raiz) / n).exists()]
    if archivos:
        return True, ", ".join(archivos)
    externos = importaciones_externas(raiz)
    if not externos:
        return True, "sin archivo de dependencias, pero solo usa la biblioteca estándar"
    return False, "sin archivo de dependencias; importa paquetes externos: " + ", ".join(sorted(externos)[:8])


def importaciones_externas(raiz):
    """Paquetes importados que no son de la biblioteca estándar ni módulos del propio repositorio."""
    import ast
    import sys
    raiz = Path(raiz)
    propios = {f.stem for f in raiz.rglob("*.py")} | {d.name for d in raiz.rglob("*") if d.is_dir()}
    externos = set()
    for f in raiz.rglob("*.py"):
        if ".git" in f.parts or ".venv" in f.parts:
            continue
        try:
            arbol = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for nodo in ast.walk(arbol):
            nombres = []
            if isinstance(nodo, ast.Import):
                nombres = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
                nombres = [nodo.module]
            for n in nombres:
                base = n.split(".")[0]
                if base not in sys.stdlib_module_names and base not in propios and base != "__future__":
                    externos.add(base)
    return externos


def licencia(raiz):
    for f in Path(raiz).iterdir():
        if f.is_file() and re.match(r"(LICEN[CS]E|COPYING)", f.name, re.I):
            primera = next((l.strip() for l in f.read_text(errors="ignore").splitlines() if l.strip()), "")
            return f"{f.name}: {primera[:60]}"
    return ""


def dias_de_actividad(raiz):
    if not (Path(raiz) / ".git").exists():
        return None
    fechas = _git(["-C", str(raiz), "log", "--format=%cI"]).split()
    if not fechas:
        return None
    ultima, primera = dt.datetime.fromisoformat(fechas[0]), dt.datetime.fromisoformat(fechas[-1])
    return (ultima - primera).days


NOMBRES_CRITERIOS = {
    "tamaño": "Tamaño",
    "sin_pruebas_previas": "Sin pruebas previas",
    "dependencias_declaradas": "Se puede instalar solo",
    "licencia": "Licencia",
    "actividad": "Al menos un año de historia",
}


def verificar(cfg, almacen, carpeta_cache):
    """Verifica los criterios de cada repositorio. Devuelve la lista de repos aptos para ejecutar."""
    crit = cfg["criterios"]
    exp = cfg["experimento"]["nombre"]
    aptos = []
    for repo in cfg["repositorios"]:
        print(f"\n>> Proyecto {repo['nombre']}")
        try:
            raiz, commit = preparar_codigo(repo, carpeta_cache)
        except Exception as e:
            almacen.registrar_criterio(exp, repo["nombre"], repo["url"], "", "descarga", False, str(e))
            print(f"   [DESCARTADO] no se pudo descargar: {e}")
            continue
        fuente = raiz / repo["fuente"]
        resultados = []

        lineas = contar_lineas(fuente) if fuente.exists() else 0
        resultados.append(("tamaño", crit["lineas_min"] <= lineas <= crit["lineas_max"],
                           f"{lineas} líneas de código en '{repo['fuente']}' (rango {crit['lineas_min']}–{crit['lineas_max']})"))
        pruebas = detectar_pruebas(raiz)
        resultados.append(("sin_pruebas_previas", not pruebas,
                           "no se encontraron pruebas" if not pruebas else "encontradas: " + ", ".join(pruebas[:5])))
        cumple_deps, detalle_deps = dependencias_declaradas(raiz)
        resultados.append(("dependencias_declaradas", cumple_deps, detalle_deps))
        lic = licencia(raiz)
        resultados.append(("licencia", bool(lic), lic or "sin archivo de licencia"))
        dias = dias_de_actividad(raiz)
        resultados.append(("actividad", dias is not None and dias >= crit["actividad_min_dias"],
                           f"{dias} días entre el primer y el último commit" if dias is not None else "sin historial git"))

        for criterio, cumple, detalle in resultados:
            almacen.registrar_criterio(exp, repo["nombre"], repo["url"], commit,
                                       criterio, cumple, detalle)
            print(f"   [{'si' if cumple else 'NO'}] {NOMBRES_CRITERIOS.get(criterio, criterio)}: {detalle}")
        almacen.registrar_criterio(exp, repo["nombre"], repo["url"], commit,
                                   "revision_manual", True,
                                   "Pendiente de revisión humana: no requiere servicios externos, credenciales ni hardware")

        cumple_todo = all(c for _, c, _ in resultados)
        if cumple_todo:
            print("   Cumple todo: entra al estudio.")
            aptos.append((repo, raiz))
        else:
            print("   Descartado. El motivo queda registrado (tabla 'seleccion').")
    return aptos


# ----------------------------------------------------------------------------
# Búsqueda de candidatos en GitHub
# ----------------------------------------------------------------------------
def buscar(salida_csv, maximo=30, estrellas_min=10, creado_desde=None):
    """
    Busca repositorios Python en GitHub y descarta automáticamente los que tienen pruebas
    (mirando el árbol de archivos sin descargar el código). El resultado es una lista de
    candidatos para revisar a mano y luego pasar a la configuración con su commit fijo.
    """
    from github import Auth, Github

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("AVISO: no hay GITHUB_TOKEN. Sin token GitHub permite solo 60 consultas por hora;\n"
              "       si se agotan, el comando se queda esperando. En Codespaces el token ya viene configurado.")
    gh = Github(auth=Auth.Token(token)) if token else Github()
    hace_un_anio = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    consulta = (f"language:python stars:>={estrellas_min} pushed:>={hace_un_anio} "
                f"created:<={hace_un_anio} fork:false archived:false size:<5000")
    if creado_desde:
        consulta += f" created:>={creado_desde}"
    print(f"Consulta a GitHub: {consulta}")
    filas, revisados = [], 0
    for r in gh.search_repositories(consulta, sort="updated", order="desc"):
        if len(filas) >= maximo or revisados >= maximo * 6:
            break
        revisados += 1
        try:
            commit = r.get_branch(r.default_branch).commit.sha
            arbol = r.get_git_tree(commit, recursive=True).tree
        except Exception as e:
            print(f"   (salteado {r.full_name}: {e})")
            continue
        rutas = [e.path for e in arbol if e.type == "blob"]
        if any(PATRON_PRUEBAS.search(p) for p in rutas):
            continue
        py = [e for e in arbol if e.type == "blob" and e.path.endswith(".py")]
        bytes_py = sum(e.size or 0 for e in py)
        lineas_estimadas = bytes_py // 32  # estimación grosera; la medición real la hace verificar()
        if not (300 <= lineas_estimadas <= 8000):
            continue
        filas.append({
            "nombre": r.full_name.replace("/", "__"), "url": r.clone_url, "commit": commit,
            "estrellas": r.stargazers_count, "creado": r.created_at.date().isoformat(),
            "ultimo_push": r.pushed_at.date().isoformat(), "archivos_py": len(py),
            "lineas_estimadas": lineas_estimadas,
            "licencia": r.license.spdx_id if r.license else "",
            "archivo_deps": any(p in rutas for p in ("requirements.txt", "pyproject.toml", "setup.py")),
        })
        print(f"   candidato: {r.full_name}  (~{lineas_estimadas} líneas, {r.stargazers_count}★)")
    Path(salida_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(salida_csv, "w", newline="", encoding="utf-8") as f:
        if filas:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
            w.writeheader()
            w.writerows(filas)
    print(f"\n{len(filas)} candidatos (de {revisados} revisados) guardados en {salida_csv}")
    return filas
