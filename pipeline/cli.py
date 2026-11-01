"""
Interfaz de línea de comandos del pipeline (Typer).

    python -m pipeline --help                    lista de comandos
    python -m pipeline verificar-entorno         chequea que todo esté instalado y que haya clave de Gemini
    python -m pipeline construir-imagen          construye la imagen Docker del ejecutor
    python -m pipeline modelos                   lista los modelos Gemini que podés usar con tu clave
    python -m pipeline probar-llm                prueba de humo contra el modelo (Gemini por defecto)
    python -m pipeline prueba                    PRIMERA PRUEBA: 1 repositorio público de GitHub + Gemini
    python -m pipeline ejecutar CONFIG.yaml      corre un experimento (M1 → M4 + almacén)
    python -m pipeline reporte                   resumen simple de la última ejecución (--tecnico: tabla completa)
    python -m pipeline ver-informe               abre el informe para leer (página HTML)
    python -m pipeline ver-pruebas               muestra el código de las pruebas generadas
    python -m pipeline buscar                    busca repositorios candidatos en GitHub (M1)
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import typer

from . import informe as mod_informe
from . import reporte as mod_reporte
from . import seleccion
from .almacen import Almacen
from .config import CLAVES_API, IMAGEN_DOCKER, MODELO_POR_DEFECTO, NIVELES_RAZONAMIENTO, RAIZ, cargar, clave_api_requerida
from .ejecucion import docker_disponible, ejecutar as mod_ejecutar, info_imagen


def _cargar_env():
    """Si existe un archivo .env en la raíz del proyecto, carga sus variables (sin pisar las existentes)."""
    archivo = RAIZ / ".env"
    if archivo.exists():
        for linea in archivo.read_text().splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                clave, valor = linea.split("=", 1)
                os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Pipeline de evaluación — Tesis FP-UNA (Soto & Arguello)")

DB = RAIZ / "resultados" / "resultados.db"
CACHE = RAIZ / "resultados" / "_repos"
IMAGEN = IMAGEN_DOCKER


def _ok(cond, texto, ayuda=""):
    typer.echo(f"  [{'OK' if cond else 'FALTA'}] {texto}" + (f"\n         -> {ayuda}" if not cond and ayuda else ""))
    return cond


@app.command("verificar-entorno")
def verificar_entorno():
    """Chequea Python, git, Docker, la imagen y la clave de Gemini."""
    typer.echo("Verificando el entorno...\n")
    _ok(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (se necesita 3.10 o más)")
    _ok(shutil.which("git") is not None, "git instalado", "instalá git")
    hay_docker = docker_disponible()
    _ok(hay_docker, "Docker instalado y corriendo", "abrí Docker Desktop / revisá la guía")
    if hay_docker:
        _ok(bool(info_imagen(IMAGEN)), f"imagen {IMAGEN} construida", "python -m pipeline construir-imagen")
    libre = shutil.disk_usage(RAIZ).free / 1e9
    _ok(libre > 5, f"espacio libre en disco: {libre:.1f} GB (recomendado > 5 GB)")
    clave = os.environ.get("GEMINI_API_KEY")
    _ok(bool(clave), "clave GEMINI_API_KEY cargada" + (f" (****{clave[-4:]})" if clave else ""),
        "conseguila en https://aistudio.google.com y cargala como Secret de Codespaces (GUIA.md, sección 4)")
    otras = [c for c in CLAVES_API if c != "GEMINI_API_KEY" and os.environ.get(c)]
    if otras:
        typer.echo(f"  [OK] otras claves presentes: {', '.join(otras)}")
    _ok(True, f"GITHUB_TOKEN {'presente' if os.environ.get('GITHUB_TOKEN') else 'ausente (solo afecta al comando buscar)'}")
    typer.echo("\nListo. Si todo dice OK, seguí con:  python -m pipeline probar-llm")


@app.command("construir-imagen")
def construir_imagen():
    """Construye la imagen Docker con Python, pytest, coverage y Qodo Cover fijado a un commit."""
    if not docker_disponible():
        typer.echo("Docker no está disponible (corré: python -m pipeline verificar-entorno).")
        raise typer.Exit(1)
    cmd = ["docker", "build", "-t", IMAGEN, "-f", str(RAIZ / "docker" / "Dockerfile"), str(RAIZ / "docker")]
    typer.echo("Construyendo imagen (la primera vez tarda entre 5 y 10 minutos)...\n  " + " ".join(cmd))
    codigo = subprocess.call(cmd)
    typer.echo("\nImagen lista." if codigo == 0 else "\nFalló la construcción: revisá el mensaje de arriba.")
    raise typer.Exit(codigo)


@app.command("modelos")
def modelos(filtro: str = typer.Option("flash", help="Texto que debe contener el nombre del modelo (vacío = todos)")):
    """Lista los modelos Gemini que tu clave puede usar, con el nombre exacto para el YAML o --modelo."""
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        typer.echo("Falta GEMINI_API_KEY (ver GUIA.md, sección 4).")
        raise typer.Exit(1)
    nombres, token = [], ""
    try:
        while True:
            url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200" + (f"&pageToken={token}" if token else "")
            pedido = urllib.request.Request(url, headers={"x-goog-api-key": clave})
            with urllib.request.urlopen(pedido, timeout=30) as r:
                datos = json.load(r)
            for m in datos.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    nombres.append((m["name"].removeprefix("models/"), m.get("displayName", "")))
            token = datos.get("nextPageToken", "")
            if not token:
                break
    except urllib.error.HTTPError as e:
        typer.echo(f"Google respondió {e.code}: {e.read().decode(errors='ignore')[:300]}")
        typer.echo("Si es 400 o 403, la clave está mal copiada o no está activa. Generá otra en https://aistudio.google.com")
        raise typer.Exit(1)
    except (urllib.error.URLError, TimeoutError) as e:
        typer.echo(f"No se pudo conectar con Google: {e}")
        raise typer.Exit(1)
    mostrados = [(n, d) for n, d in sorted(nombres) if filtro.lower() in n.lower()]
    typer.echo(f"Modelos Gemini disponibles para tu clave (filtro: '{filtro}'). Usá el nombre con 'gemini/' adelante:\n")
    for n, d in mostrados:
        typer.echo(f"  gemini/{n:<40} {d}")
    if not mostrados:
        typer.echo("  (ninguno con ese filtro; probá:  python -m pipeline modelos --filtro '')")
    typer.echo(f"\nEl modelo por defecto del proyecto es {MODELO_POR_DEFECTO}.")
    typer.echo("Para usar otro sin tocar archivos:  python -m pipeline prueba --modelo gemini/NOMBRE")


def _chequear_llm(modelo, api_base="", sin_docker=False, mostrar=True, razonamiento=""):
    """
    Manda un mensaje corto al modelo, con el mismo nivel de razonamiento que va a usar el experimento.
    Devuelve el código de salida (0 = funciona).
    """
    codigo_py = (
        "import litellm,sys\n"
        f"kw=dict(model={modelo!r}, messages=[{{'role':'user','content':'Respondé solo: funciona'}}], max_tokens=2048)\n"
        f"kw.update(api_base={api_base!r}) if {api_base!r} else None\n"
        f"kw.update(reasoning_effort={razonamiento!r}) if {razonamiento!r} else None\n"
        "r=litellm.completion(**kw)\n"
        "print('RESPUESTA DEL MODELO:', r.choices[0].message.content)\n"
        "d=getattr(r.usage,'completion_tokens_details',None)\n"
        "print('tokens usados:', r.usage.total_tokens, '(entrada', r.usage.prompt_tokens, '/ salida',"
        " r.usage.completion_tokens, '/ de razonamiento', (getattr(d,'reasoning_tokens',0) or 0), ')')\n")
    if sin_docker:
        cmd = [sys.executable, "-c", codigo_py]
    else:
        cmd = ["docker", "run", "--rm"]
        for c in CLAVES_API:
            if os.environ.get(c):
                cmd += ["-e", c]
        cmd += [IMAGEN, "/opt/qodo/bin/python", "-c", codigo_py]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        if mostrar:
            typer.echo("   El modelo no respondió en 3 minutos.")
        return 1
    if mostrar:
        lineas = [l for l in (r.stdout + "\n" + r.stderr).splitlines() if l.strip()]
        if r.returncode == 0:
            typer.echo("   " + "\n   ".join(l for l in lineas if l.startswith(("RESPUESTA", "tokens"))))
        else:
            typer.echo("   No se pudo hablar con el modelo. Últimas líneas del error:")
            typer.echo("   " + "\n   ".join(lineas[-6:]))
            texto = (r.stdout + r.stderr).lower()
            if "429" in texto or "resource_exhausted" in texto or "quota" in texto:
                typer.echo("   -> Límite del plan gratuito: esperá un minuto y reintentá (o probá otro modelo con 'modelos').")
            elif "404" in texto or "not found" in texto or "notfound" in texto:
                typer.echo("   -> Ese nombre de modelo no existe. Mirá los vigentes con:  python -m pipeline modelos")
            elif "api key" in texto or "api_key" in texto or "401" in texto or "403" in texto or "permission" in texto:
                typer.echo("   -> Problema con la clave: revisá que GEMINI_API_KEY esté bien copiada (sin espacios).")
            elif razonamiento and ("thinking" in texto or "reasoning" in texto or "400" in texto):
                typer.echo(f"   -> El modelo no aceptó el nivel de razonamiento '{razonamiento}'. Probá otro con "
                           "--razonamiento, o dejá  razonamiento: \"\"  en el YAML (sección herramienta).")
    return r.returncode


@app.command("probar-llm")
def probar_llm(modelo: str = typer.Option(MODELO_POR_DEFECTO, help="Ej: gemini/gemini-2.5-flash  u  openai/gpt-4o-mini"),
               api_base: str = typer.Option("", help="Solo para endpoints compatibles con OpenAI"),
               sin_docker: bool = typer.Option(False, help="Usar litellm local en vez del contenedor"),
               razonamiento: str = typer.Option("", help="Nivel de razonamiento: minimal|low|medium|high|disable "
                                                         "(vacío = el del modelo)")):
    """Manda un mensaje corto al modelo para confirmar que la clave y el nombre del modelo funcionan."""
    if razonamiento not in NIVELES_RAZONAMIENTO:
        typer.echo(f"Nivel de razonamiento inválido. Opciones: {', '.join(n for n in NIVELES_RAZONAMIENTO if n)}")
        raise typer.Exit(1)
    variable = clave_api_requerida(modelo)
    if variable and not os.environ.get(variable):
        typer.echo(f"Falta la variable {variable} (la clave de API). Ver GUIA.md, sección 4.")
        raise typer.Exit(1)
    typer.echo(f"Probando {modelo}" + (f" con razonamiento '{razonamiento}'" if razonamiento else "") + " ...")
    codigo = _chequear_llm(modelo, api_base, sin_docker, razonamiento=razonamiento)
    typer.echo("\nListo: la IA responde." if codigo == 0 else "\nLa prueba falló: arreglá esto antes de correr el experimento.")
    raise typer.Exit(codigo)


@app.command()
def buscar(maximo: int = typer.Option(20, help="Cantidad máxima de candidatos"),
           estrellas_min: int = typer.Option(10, help="Estrellas mínimas en GitHub"),
           grupo: str = typer.Option("A", help="A = sin pruebas; B = con pruebas"),
           salida: Optional[Path] = typer.Option(None, help="CSV de salida (por defecto resultados/candidatos_<grupo>.csv)")):
    """M1: busca repositorios candidatos (Python, activos; sin pruebas o con pruebas según el grupo) en GitHub."""
    grupo = grupo.strip().upper()
    if grupo not in ("A", "B"):
        typer.echo("El grupo tiene que ser A o B.")
        raise typer.Exit(1)
    salida = salida or RAIZ / "resultados" / f"candidatos_{grupo}.csv"
    seleccion.buscar(salida, maximo=maximo, estrellas_min=estrellas_min, grupo=grupo)


@app.command()
def seleccionar(config: Path):
    """M1: verifica los criterios de inclusión de los repositorios de una configuración."""
    cfg = cargar(config)
    aptos = seleccion.verificar(cfg, Almacen(DB), CACHE)
    typer.echo(f"\n{len(aptos)} de {len(cfg['repositorios'])} repositorios aptos.")


def _cerrar_ejecucion(almacen, ejecucion_id, cfg=None):
    """Al terminar: exporta CSV, genera el informe HTML y muestra el resumen simple."""
    if cfg:
        mod_reporte.exportar_csv(almacen, ejecucion_id,
                                 RAIZ / cfg["experimento"]["carpeta_resultados"] / cfg["experimento"]["nombre"] / "csv",
                                 silencioso=True)
    ruta = mod_informe.generar_html(almacen, ejecucion_id)
    mod_reporte.resumen(almacen, ejecucion_id, ruta.relative_to(RAIZ) if ruta else None)
    return ruta


@app.command()
def ejecutar(config: Path,
             modelo: Optional[str] = typer.Option(None, help="Reemplaza el modelo del YAML"),
             api_base: str = typer.Option("", help="api_base para el modelo indicado con --modelo"),
             sin_docker: bool = typer.Option(False, help="Modo depuración: sin contenedores (sin aislamiento)"),
             detalle: bool = typer.Option(False, help="Mostrar comandos y salida cruda de Qodo Cover"),
             chequeo: bool = typer.Option(True, "--chequeo/--sin-chequeo",
                                          help="Antes de empezar, confirmar que la IA responde")):
    """Corre el experimento definido en CONFIG: selección → preparación → cobertura → generación → registro."""
    if detalle:
        os.environ["DETALLE"] = "1"
    cfg = cargar(config, modelo, api_base)
    variable = clave_api_requerida(cfg["llm"]["modelo"])
    if variable and not os.environ.get(variable):
        typer.echo(f"Falta la clave de API: el modelo {cfg['llm']['modelo']} necesita la variable {variable}.")
        typer.echo("Cargala como Secret de Codespaces (GUIA.md, sección 4) y reiniciá el Codespace.")
        raise typer.Exit(1)
    if not (cfg["llm"]["entrada_usd_por_millon"] or cfg["llm"]["salida_usd_por_millon"]):
        typer.echo("AVISO: los precios del modelo en el YAML están en 0, así que el límite de costo por proyecto "
                   "(RNF-04) no va a actuar. Cargá entrada_usd_por_millon y salida_usd_por_millon.\n")
    almacen = Almacen(DB)
    typer.echo("=" * 72)
    typer.echo(f"PIPELINE DE EVALUACIÓN  experimento: {cfg['experimento']['nombre']}")
    n_a = sum(1 for r in cfg["repositorios"] if r["grupo"] == "A")
    n_b = len(cfg["repositorios"]) - n_a
    h = cfg["herramienta"]
    typer.echo(f"Modelo: {cfg['llm']['modelo']}   ·   repositorios públicos de GitHub: {n_a} del grupo A "
               f"(sin pruebas preexistentes) y {n_b} del grupo B (con suite preexistente)")
    typer.echo(f"Parámetros: razonamiento '{h['razonamiento'] or 'por defecto'}' · diagnóstico de fallas "
               f"{'local' if h['analisis_fallas'] == 'local' else 'con el modelo'} · hasta {h['max_modulos']} "
               f"módulos por proyecto, {h['max_iteraciones']} iteraciones · tope US$ {cfg['limites']['limite_costo_repo_usd']} "
               "por proyecto")
    typer.echo("=" * 72)
    if chequeo:
        typer.echo("\nVerificación previa: disponibilidad del modelo con la configuración del experimento")
        if _chequear_llm(cfg["llm"]["modelo"], cfg["llm"]["api_base"], sin_docker,
                         razonamiento=cfg["herramienta"]["razonamiento"]) != 0:
            typer.echo("\nNo se sigue: sin una IA que responda el experimento no produce datos. "
                       "Arreglalo y reintentá (o saltá este chequeo con --sin-chequeo).")
            raise typer.Exit(1)
    typer.echo("\nPaso 1/5  Selección: verificación de los criterios de inclusión")
    aptos = seleccion.verificar(cfg, almacen, CACHE)
    if not aptos:
        typer.echo("Ningún repositorio cumple los criterios; no hay nada para ejecutar.")
        raise typer.Exit(1)
    ejecucion_id = mod_ejecutar(cfg, almacen, aptos, sin_docker=sin_docker)
    _cerrar_ejecucion(almacen, ejecucion_id, cfg)


@app.command()
def prueba(modelo: Optional[str] = typer.Option(None, help="Reemplaza el modelo (ej: gemini/gemini-3-flash)"),
           sin_docker: bool = typer.Option(False, help="Correr sin Docker (plan B)"),
           detalle: bool = typer.Option(False, help="Mostrar comandos y salida cruda de Qodo Cover")):
    """PRUEBA RÁPIDA: 1 repositorio del grupo A y 1 del grupo B + Gemini (config/prueba.yaml). Unos 10 minutos."""
    ejecutar(RAIZ / "config" / "prueba.yaml", modelo=modelo, api_base="", sin_docker=sin_docker,
             detalle=detalle, chequeo=True)


@app.command()
def reporte(ejecucion: Optional[int] = typer.Option(None, help="Número de ejecución (por defecto, la última)"),
            tecnico: bool = typer.Option(False, help="Tabla técnica completa (abreviaturas, tokens, categorías)"),
            csv: Optional[Path] = typer.Option(None, help="Carpeta donde exportar CSV")):
    """Resumen de una ejecución. Regenera también el informe HTML."""
    almacen = Almacen(DB)
    if tecnico:
        mod_reporte.generar(almacen, ejecucion, csv)
        return
    if csv:
        mod_reporte.exportar_csv(almacen, ejecucion or mod_reporte._ultima(almacen, None), csv)
    _cerrar_ejecucion(almacen, mod_reporte._ultima(almacen, ejecucion))


@app.command("ver-informe")
def ver_informe(ejecucion: Optional[int] = typer.Option(None, help="Número de ejecución (por defecto, la última)"),
                puerto: int = typer.Option(8000, help="Puerto para servir el informe (Codespaces)")):
    """Genera el informe HTML de una ejecución y lo abre en el navegador."""
    almacen = Almacen(DB)
    ruta = mod_informe.generar_html(almacen, mod_reporte._ultima(almacen, ejecucion))
    if not ruta:
        typer.echo("Todavía no hay ejecuciones registradas. Corré primero: python -m pipeline prueba")
        raise typer.Exit(1)
    typer.echo(f"Informe: {ruta.relative_to(RAIZ)}")
    en_codespaces = os.environ.get("CODESPACES") == "true"
    if not en_codespaces:
        import webbrowser
        if webbrowser.open(ruta.as_uri()):
            typer.echo("Abierto en el navegador.")
            return
    # Codespaces (o una máquina sin navegador): se sirve la carpeta por HTTP y se abre desde ahí
    import functools
    import http.server

    class Silencioso(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    manejador = functools.partial(Silencioso, directory=str(ruta.parent))
    typer.echo(f"\nSirviendo el informe en el puerto {puerto}.")
    if en_codespaces:
        typer.echo("En Codespaces va a aparecer un aviso abajo a la derecha: apretá 'Open in Browser'.")
        typer.echo("(Si no aparece: pestaña PORTS, fila del puerto, ícono del globo.)")
    typer.echo(f"Dirección: http://localhost:{puerto}/{ruta.name}")
    typer.echo("Para cerrarlo: Ctrl + C")
    try:
        http.server.ThreadingHTTPServer(("127.0.0.1" if not en_codespaces else "0.0.0.0", puerto), manejador).serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nListo.")


@app.command("ver-pruebas")
def ver_pruebas(categoria: Optional[str] = typer.Option(None, help="aceptada | sin_aporte | falla | no_compila"),
                limite: int = typer.Option(5), ejecucion: Optional[int] = typer.Option(None)):
    """Muestra el código de pruebas generadas y cómo se clasificaron."""
    almacen = Almacen(DB)
    if ejecucion is None:
        ejecucion = almacen.consultar("SELECT MAX(id) AS id FROM ejecuciones")[0]["id"]
    sql = ("SELECT p.* FROM pruebas p JOIN corridas c ON p.corrida_id=c.id WHERE c.ejecucion_id=?"
           + (" AND p.categoria=?" if categoria else "") + " ORDER BY p.id LIMIT ?")
    params = (ejecucion, categoria, limite) if categoria else (ejecucion, limite)
    for p in almacen.consultar(sql, params):
        typer.echo(f"\n--- prueba #{p['id']} · {p['repo']} · {p['modulo']} · categoría: {p['categoria'].upper()} "
                   f"· compila={p['compila']} pasa={p['pasa']} aporta={p['aporta']} aserción={p['tiene_asercion']}")
        if p["imports"] and p["imports"].strip() not in ('', '""'):
            typer.echo(p["imports"].rstrip())
        typer.echo(p["codigo_prueba"].rstrip())
        if p["categoria"] in ("falla", "no_compila"):
            error = [l for l in (p["salida_error"] or "").splitlines() if "Error" in l or "assert" in l]
            if error:
                typer.echo(f"   motivo: {error[-1].strip()[:120]}")


if __name__ == "__main__":
    app()
