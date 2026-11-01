"""
ORQUESTADOR. Corre en la máquina anfitriona y coordina todo:
  - por cada repositorio apto y cada corrida, lanza un contenedor NUEVO y desechable (RNF-02)
  - le pasa una "orden de trabajo" (JSON) con los parámetros fijos de la configuración (RNF-01, RNF-05)
  - aplica el límite de tiempo por repositorio y, si un repositorio falla, lo registra y sigue (RF-13)
  - guarda el resultado de cada corrida en la base SQLite (RF-11, RNF-03)
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import clasificacion
from .config import CLAVES_API, RAIZ, clave_api_requerida  # CLAVES_API: variables que se pasan al contenedor SIN escribirlas en disco


def docker_disponible():
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True).returncode == 0


def info_imagen(imagen):
    r = subprocess.run(["docker", "image", "inspect", imagen, "--format", "{{.Id}}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def commit_pipeline():
    r = subprocess.run(["git", "-C", str(RAIZ), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    sucio = subprocess.run(["git", "-C", str(RAIZ), "status", "--porcelain"], capture_output=True, text=True).stdout
    return (r.stdout.strip() + ("-modificado" if sucio.strip() else "")) if r.returncode == 0 else "sin-git"


def info_maquina():
    datos = {"sistema": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()}
    try:
        datos["memoria_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        pass
    if shutil.which("docker"):
        datos["docker"] = subprocess.run(["docker", "--version"], capture_output=True, text=True).stdout.strip()
    return datos


def orden_de_trabajo(cfg, repo, corrida):
    return {
        "nombre": repo["nombre"],
        "grupo": repo.get("grupo", "A"),
        "corrida": corrida,
        "fuente": repo["fuente"],
        "archivos_prueba": repo.get("archivos_prueba") or {},
        "criterios": {k: cfg["criterios"][k] for k in ("cobertura_a_max", "cobertura_b_min", "cobertura_b_max")},
        "instalacion_extra": repo.get("instalacion_extra", []),
        "herramienta": cfg["herramienta"],
        "llm": {"modelo": cfg["llm"]["modelo"], "api_base": cfg["llm"]["api_base"]},
        "costo": {
            "entrada_usd_por_millon": cfg["llm"]["entrada_usd_por_millon"],
            "salida_usd_por_millon": cfg["llm"]["salida_usd_por_millon"],
            "entrada_cache_usd_por_millon": cfg["llm"].get("entrada_cache_usd_por_millon"),
            "limite_repo_usd": cfg["limites"]["limite_costo_repo_usd"],
        },
    }


def _correr_en_vivo(cmd, timeout_seg, env=None, nombre_contenedor=None):
    """Ejecuta mostrando la salida en vivo (para que se vea qué está pasando)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    limite = time.time() + timeout_seg
    try:
        for linea in proc.stdout:
            print("   " + linea.rstrip(), flush=True)
            if time.time() > limite:
                raise subprocess.TimeoutExpired(cmd, timeout_seg)
        return proc.wait(timeout=max(1, limite - time.time()))
    except subprocess.TimeoutExpired:
        if nombre_contenedor:
            subprocess.run(["docker", "kill", nombre_contenedor], capture_output=True)
        proc.kill()
        return -999


def correr_en_docker(cfg, repo, raiz_codigo, carpeta_corrida, corrida):
    imagen = cfg["docker"]["imagen"]
    nombre = f"tesis-{repo['nombre'].lower().replace('_', '-')}-c{corrida}-{int(time.time())}"[:60]
    cmd = ["docker", "run", "--rm", "--name", nombre,
           f"--cpus={cfg['limites']['cpus']}", f"--memory={cfg['limites']['memoria']}",
           "-v", f"{raiz_codigo}:/codigo:ro",
           "-v", f"{RAIZ / 'scripts'}:/scripts:ro",
           "-v", f"{carpeta_corrida}:/salida"]
    for clave in CLAVES_API:
        if os.environ.get(clave):
            cmd += ["-e", clave]  # solo el nombre: Docker toma el valor del entorno, no queda en el comando
    interno = "python /scripts/trabajo_repo.py /salida/orden.json /salida"
    if os.environ.get("DETALLE"):
        cmd += ["-e", "DETALLE"]
    # Los archivos que crea el contenedor quedan a nombre de root; al terminar se devuelven al usuario
    # de la máquina anfitriona para poder borrarlos o moverlos sin sudo.
    if hasattr(os, "getuid"):
        interno += f"; rc=$?; chown -R {os.getuid()}:{os.getgid()} /salida; exit $rc"
    cmd += [imagen, "sh", "-c", interno]
    return _correr_en_vivo(cmd, cfg["limites"]["tiempo_max_repo_min"] * 60, nombre_contenedor=nombre)


def correr_sin_docker(cfg, repo, raiz_codigo, carpeta_corrida, corrida):
    """
    Modo de DEPURACIÓN: ejecuta el trabajo directamente en la máquina, sin contenedor.
    No hay aislamiento (no cumple RNF-02); sirve solo para probar o como plan B.
    Requiere cover-agent instalado en el mismo entorno (pip install -r requirements-sin-docker.txt).
    """
    qodo = shutil.which("cover-agent")
    if not qodo:
        raise SystemExit("Modo sin Docker: no se encontró 'cover-agent'. Instalalo con:\n"
                         "  pip install -r requirements-sin-docker.txt")
    # Los mismos ajustes del estudio que tiene la imagen Docker (idempotente: si ya están, no cambia nada)
    py_qodo = Path(qodo).parent / "python"
    r = subprocess.run([str(py_qodo if py_qodo.exists() else sys.executable), str(RAIZ / "docker" / "parche_qodo.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("No se pudieron aplicar los ajustes del estudio a cover-agent:\n" + r.stdout + r.stderr)
    trabajo = Path(tempfile.mkdtemp(prefix=f"tesis-{repo['nombre']}-"))
    orden = json.loads((carpeta_corrida / "orden.json").read_text())
    orden.update(directorio_codigo=str(raiz_codigo), directorio_trabajo=str(trabajo / "repo"))
    (carpeta_corrida / "orden.json").write_text(json.dumps(orden, indent=2, ensure_ascii=False))
    env = dict(os.environ, PYTHON_REPO=sys.executable, QODO_BIN=qodo)
    try:
        return _correr_en_vivo([sys.executable, str(RAIZ / "scripts" / "trabajo_repo.py"),
                                str(carpeta_corrida / "orden.json"), str(carpeta_corrida)],
                               cfg["limites"]["tiempo_max_repo_min"] * 60, env=env)
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def ejecutar(cfg, almacen, aptos, sin_docker=False):
    exp = cfg["experimento"]["nombre"]
    base = RAIZ / cfg["experimento"]["carpeta_resultados"] / exp
    if not sin_docker:
        if not docker_disponible():
            raise SystemExit("Docker no está disponible. Revisá la guía (paso 'Verificar el entorno') "
                             "o usá --sin-docker para el modo de depuración.")
        if not info_imagen(cfg["docker"]["imagen"]):
            raise SystemExit(f"No existe la imagen {cfg['docker']['imagen']}. Construila con:\n"
                             "  python -m pipeline construir-imagen")
    variable = clave_api_requerida(cfg["llm"]["modelo"])
    if variable and not os.environ.get(variable):
        raise SystemExit(f"Falta la clave de API: el modelo {cfg['llm']['modelo']} necesita la variable {variable}.\n"
                         "Cargala como Secret de Codespaces (o con export / archivo .env). Paso a paso en GUIA.md, "
                         "sección 'Conseguir y cargar la clave de Gemini'.")
    if not variable and not any(os.environ.get(c) for c in CLAVES_API):
        raise SystemExit("No hay ninguna clave de API en el entorno "
                         f"({', '.join(CLAVES_API)}). Revisá GUIA.md, sección 'Conseguir y cargar la clave de Gemini'.")

    ejecucion_id = almacen.nueva_ejecucion(cfg, commit_pipeline(),
                                           "sin-docker" if sin_docker else info_imagen(cfg["docker"]["imagen"]),
                                           info_maquina())
    print(f"\nPasos 2 a 5, proyecto por proyecto (queda registrado como ejecución número {ejecucion_id})")

    detener = False
    for repo, raiz_codigo in aptos:
        if detener:
            break
        for corrida in range(1, cfg["experimento"]["corridas_por_repo"] + 1):
            carpeta = base / repo["nombre"] / f"corrida_{corrida}"
            if carpeta.exists():  # resultados de una ejecución anterior de la misma corrida
                try:
                    shutil.rmtree(carpeta)
                except PermissionError:
                    carpeta.rename(carpeta.with_name(f"{carpeta.name}_anterior_{int(time.time())}"))
            carpeta.mkdir(parents=True)
            (carpeta / "orden.json").write_text(
                json.dumps(orden_de_trabajo(cfg, repo, corrida), indent=2, ensure_ascii=False))
            total_c = cfg["experimento"]["corridas_por_repo"]
            print(f"\n>> Proyecto {repo['nombre']}  (grupo {repo.get('grupo', 'A')})"
                  + (f"  (corrida {corrida} de {total_c})" if total_c > 1 else ""))
            inicio = time.time()
            try:
                codigo = (correr_sin_docker if sin_docker else correr_en_docker)(cfg, repo, raiz_codigo, carpeta, corrida)
            except Exception as e:  # RF-13: un fallo no detiene el experimento
                codigo = -1
                print(f"   ERROR del orquestador: {e}")
            archivo = carpeta / "resultado.json"
            if archivo.exists():
                resultado = json.loads(archivo.read_text())
                if codigo == -999:
                    resultado["estado"] = "tiempo_agotado"
            else:
                resultado = {"repo": repo["nombre"], "corrida": corrida,
                             "estado": "tiempo_agotado" if codigo == -999 else "error_contenedor",
                             "duracion_seg": round(time.time() - inicio, 1),
                             "error": f"El contenedor terminó con código {codigo} sin dejar resultado.json"}
            resultado.setdefault("grupo", repo.get("grupo", "A"))
            corrida_id = almacen.registrar_corrida(ejecucion_id, exp, resultado, carpeta, cfg["llm"]["modelo"],
                                                   clasificacion.clasificar)
            if resultado["estado"] in ("fuera_de_rango", "suite_falla", "sin_modulos"):
                # Criterio que solo se puede verificar con el proyecto instalado: queda en la tabla de selección
                almacen.registrar_criterio(exp, repo["nombre"], repo["url"], repo["commit"], "cobertura_inicial",
                                           False, resultado.get("error", resultado["estado"]))
            cats = {f["categoria"]: f["n"] for f in almacen.consultar(
                "SELECT categoria, COUNT(*) AS n FROM pruebas WHERE corrida_id=? GROUP BY categoria", (corrida_id,))}
            if cats:
                print(f"   Clasificación: {cats.get('aceptada', 0)} aceptadas, {cats.get('sin_aporte', 0)} válidas sin incremento "
                      f"de cobertura, {cats.get('falla', 0)} fallidas o inestables, {cats.get('no_compila', 0)} no compilan")
            if resultado["estado"] != "completo":
                print(f"   Terminó sin completarse ({resultado['estado']}): queda registrado.")
            print(f"   Tiempo: {round(time.time() - inicio)} segundos")
            if resultado.get("error_llm_tipo") in ("cuota", "clave", "modelo"):
                print(f"\n   SE DETIENE EL EXPERIMENTO: la IA no responde ({resultado['error_llm_tipo']}). "
                      "Los proyectos que faltan no se procesan para no registrar datos vacíos.")
                if resultado["error_llm_tipo"] == "cuota":
                    print("   Cuota agotada: en el plan gratuito, esperar a que se renueve o pasar al plan pago.")
                detener = True
                break
    almacen.cerrar_ejecucion(ejecucion_id)
    return ejecucion_id
