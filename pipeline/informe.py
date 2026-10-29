"""
INFORME LEGIBLE de una ejecución (página HTML autocontenida).

Está escrito para alguien que NO conoce el proyecto ni el código: explica qué hizo el sistema,
muestra el "embudo" de pruebas de cada proyecto, da ejemplos reales de pruebas buenas y malas,
y compara con Meta solo cuando el modelo es real. El reporte técnico (tabla de terminal y CSV)
sigue existiendo: python -m pipeline reporte --tecnico

No usa recursos externos (fuentes, scripts, imágenes): se abre sin internet.
"""

import html
import json
import statistics as st
import textwrap
from pathlib import Path

from .config import RAIZ
from .reporte import REFERENCIA_META

ETAPAS = [  # (clave, título, explicación)
    ("escritas", "Escritas por la IA", "Todas las pruebas que la IA propuso, buenas y malas."),
    ("compila", "Se pudieron ejecutar", "Python las aceptó: no tienen errores de escritura."),
    ("pasa", "Pasaron siempre", "Dieron bien en cada una de las repeticiones."),
    ("aporta", "Sirvieron", "Además revisan código que antes no revisaba nadie."),
]

CATEGORIAS = {
    "aceptada": ("Sirvió", "Funciona siempre y revisa código que nadie revisaba. "
                           "Es la única categoría que la herramienta conserva."),
    "sin_aporte": ("Funciona, pero no suma", "Se ejecuta y pasa, pero revisa código que otra prueba ya revisaba. "
                                             "La herramienta la descarta."),
    "falla": ("No pasó", "Python la pudo ejecutar, pero el resultado no fue el esperado "
                         "(o no fue igual en todas las repeticiones). La herramienta la descarta."),
    "no_compila": ("Mal escrita", "Python ni siquiera la pudo ejecutar: usa algo que no existe o tiene un "
                                  "error de escritura. La herramienta la descarta."),
    "error": ("Sin evaluar", "La herramienta no llegó a evaluarla."),
}

ESTADOS = {
    "completo": "Procesado completo",
    "no_preparable": "No se pudo preparar: falló la instalación de lo que el proyecto necesita. "
                     "Queda registrado y se sigue con el siguiente; a cuántos proyectos les pasa es parte del estudio.",
    "tiempo_agotado": "Se cortó por superar el tiempo máximo por proyecto.",
    "error_contenedor": "El entorno aislado terminó con un error antes de dejar resultados.",
    "error_cobertura_inicial": "No se pudo medir la cobertura inicial.",
    "error_llm": "La IA no respondió (cuota agotada, servicio saturado, clave o modelo inválidos). "
                 "Esta corrida no es un dato válido y hay que repetirla.",
}

CRITERIOS = {
    "tamaño": "Tamaño de proyecto chico (500 a 5.000 líneas)",
    "sin_pruebas_previas": "No tiene pruebas previas",
    "dependencias_declaradas": "Se puede instalar de forma automática",
    "licencia": "Su licencia permite usarlo para investigar",
    "actividad": "Tiene al menos un año de historia",
    "revision_manual": "No necesita servicios externos ni credenciales (revisión manual)",
    "descarga": "Se pudo descargar",
}

HIPOTESIS = {"compila": ("menor", "H1"), "pasa": ("menor", "H1"), "aporta": ("mayor", "H2")}


def _n(v, dec=1):
    """Número con coma decimal (formato local)."""
    if v is None:
        return "—"
    return f"{v:.{dec}f}".replace(".", ",")


def _pct(a, b):
    return 100 * a / b if b else None


def _e(texto):
    return html.escape(str(texto if texto is not None else ""))


def _datos(almacen, ejecucion_id):
    ej = almacen.consultar("SELECT * FROM ejecuciones WHERE id=?", (ejecucion_id,))[0]
    corridas = almacen.consultar("SELECT * FROM corridas WHERE ejecucion_id=? ORDER BY repo, corrida", (ejecucion_id,))
    repos = {}
    for c in corridas:
        pruebas = almacen.consultar("SELECT * FROM pruebas WHERE corrida_id=? ORDER BY id", (c["id"],))
        r = repos.setdefault(c["repo"], {"nombre": c["repo"], "corridas": [], "pruebas": []})
        r["corridas"].append(c)
        r["pruebas"].extend(pruebas)
    for r in repos.values():
        p = r["pruebas"]
        r["conteo"] = {
            "escritas": len(p),
            "compila": sum(x["compila"] for x in p),
            "pasa": sum(x["pasa"] for x in p),
            "aporta": sum(x["aporta"] for x in p),
        }
        r["aceptadas_con_asercion"] = sum(1 for x in p if x["aporta"] and x["tiene_asercion"])
        # Porcentaje del proyecto = promedio de sus corridas (unidad de análisis: el repositorio, sección 3.4.6)
        r["pct"] = {}
        for k in ("compila", "pasa", "aporta"):
            vals = []
            for c in r["corridas"]:
                pc = [x for x in p if x["corrida_id"] == c["id"]]
                if pc:
                    vals.append(_pct(sum(x[k] for x in pc), len(pc)))
            r["pct"][k] = st.mean(vals) if vals else None
        completas = [c for c in r["corridas"] if c["estado"] == "completo"]
        r["cob_ini"] = st.mean([c["cobertura_inicial"] for c in completas]) if completas else None
        r["cob_fin"] = st.mean([c["cobertura_final"] for c in completas
                                if c["cobertura_final"] is not None]) if completas else None
        sem = [c.get("cobertura_semillas") for c in completas if c.get("cobertura_semillas") is not None]
        r["cob_sem"] = st.mean(sem) if sem else None
        r["estados"] = [c["estado"] for c in r["corridas"]]
    # Criterios de selección: el registro más reciente de cada (repo, criterio)
    sel = almacen.consultar(
        "SELECT s.* FROM seleccion s JOIN (SELECT repo, criterio, MAX(id) AS mid FROM seleccion "
        "WHERE experimento=? GROUP BY repo, criterio) u ON s.id=u.mid ORDER BY s.id", (ej["experimento"],))
    for s in sel:
        if s["repo"] in repos:
            repos[s["repo"]].setdefault("criterios", []).append(s)
    return ej, list(repos.values())


# -----------------------------------------------------------------------------------------------
# Piezas de la página
# -----------------------------------------------------------------------------------------------
def _embudo(r):
    total = r["conteo"]["escritas"]
    filas = []
    for i, (clave, titulo, ayuda) in enumerate(ETAPAS):
        cant = r["conteo"][clave]
        ancho = 100 * cant / total if total else 0
        pct = 100.0 if clave == "escritas" else r["pct"].get(clave)
        marca = ""
        if clave in REFERENCIA_META:
            ref = REFERENCIA_META[clave]
            marca = (f'<span class="meta-marca" style="left:{ref}%" aria-hidden="true">'
                     f'<span>Meta {ref:.0f}%</span></span>')
        filas.append(f"""
        <div class="etapa etapa-{i}">
          <div class="etapa-texto">
            <span class="etapa-titulo">{_e(titulo)}</span>
            <span class="etapa-ayuda">{_e(ayuda)}</span>
          </div>
          <div class="etapa-barra" role="img" aria-label="{_e(titulo)}: {cant} de {total}">
            <div class="relleno" style="width:{max(ancho, 0.6):.1f}%"></div>
            {marca}
          </div>
          <div class="etapa-valor"><strong>{cant}</strong> <span>{'' if clave == 'escritas' else _n(pct, 0) + '%'}</span></div>
        </div>""")
    return "\n".join(filas)


def _cobertura(r):
    ini, fin = r["cob_ini"], r["cob_fin"]
    if ini is None:
        return ""
    fin_v = fin if fin is not None else 0
    sem = r.get("cob_sem")
    fila_sem = nota_sem = ""
    if sem is not None:
        fila_sem = (f'<div class="cob-fila"><span>Solo importando</span><div class="cob-barra">'
                    f'<div style="width:{max(sem, 0.4):.1f}%"></div></div><strong>{_n(sem)}%</strong></div>')
        nota_sem = (f'<p class="nota">Importar los archivos, sin ninguna prueba, ya cubre {_n(sem)}%. '
                    f'Lo que aportan las pruebas de la IA es la diferencia: <strong>{_n(fin_v - sem)} puntos</strong>.</p>')
    return f"""
      <div class="cobertura">
        <p class="cob-titulo">Qué parte del código queda revisada por pruebas</p>
        <div class="cob-fila"><span>Sin pruebas</span><div class="cob-barra"><div style="width:{max(ini, 0.4):.1f}%"></div></div><strong>{_n(ini)}%</strong></div>
        {fila_sem}
        <div class="cob-fila"><span>Con la IA</span><div class="cob-barra despues"><div style="width:{max(fin_v, 0.4):.1f}%"></div></div><strong>{_n(fin)}%</strong></div>
        {nota_sem}
      </div>"""


def _criterios(r):
    if not r.get("criterios"):
        return ""
    items = "".join(
        f'<li class="{"si" if c["cumple"] else "no"}"><span class="tilde" aria-hidden="true">{"✓" if c["cumple"] else "✗"}</span>'
        f'<span>{_e(CRITERIOS.get(c["criterio"], c["criterio"]))}<small>{_e(c["detalle"])}</small></span></li>'
        for c in r["criterios"])
    return f"""
      <details class="criterios">
        <summary>Por qué este proyecto entró al estudio</summary>
        <ul>{items}</ul>
      </details>"""


def _tarjeta_repo(r, n_corridas):
    c = r["conteo"]
    errores = [e for e in r["estados"] if e != "completo"]
    aviso = ""
    if errores:
        aviso = "".join(f'<p class="aviso-estado">{_e(ESTADOS.get(e, e))}</p>' for e in sorted(set(errores)))
    if c["escritas"] == 0:
        cuerpo = '<p class="vacio">No hubo pruebas para clasificar en este proyecto.</p>'
    else:
        verificacion = ""
        if c["aporta"]:
            sin = c["aporta"] - r["aceptadas_con_asercion"]
            verificacion = (f'<p class="nota-asercion">De las {c["aporta"]} que sirvieron, '
                            f'<strong>{r["aceptadas_con_asercion"]}</strong> comprueban un resultado de verdad'
                            + (f' y <strong>{sin}</strong> solo ejecutan código sin comprobar nada '
                               f'(suman cobertura, pero no detectarían un error).' if sin else '.')
                            + '</p>')
        nota_corridas = (f'<p class="nota">Cantidades: suma de las {n_corridas} corridas. '
                         f'Porcentajes: promedio de las corridas.</p>' if n_corridas > 1 else "")
        cuerpo = f"""
      <p class="resumen-repo">La IA escribió <strong>{c['escritas']}</strong> pruebas y
        <strong>{c['aporta']}</strong> terminaron sirviendo.</p>
      <div class="embudo">{_embudo(r)}</div>
      {verificacion}{nota_corridas}"""
    return f"""
    <article class="repo">
      <h3>{_e(r['nombre'])}</h3>
      {aviso}{cuerpo}{_cobertura(r)}{_criterios(r)}
    </article>"""


def _comparacion(repos):
    filas = []
    n = 0
    for clave, titulo, _ in ETAPAS[1:]:
        vals = [r["pct"][clave] for r in repos if r["pct"].get(clave) is not None]
        if not vals:
            continue
        n = len(vals)
        med = st.median(vals)
        ref = REFERENCIA_META[clave]
        dif = med - ref
        sentido, hip = HIPOTESIS[clave]
        va = (dif < 0) if sentido == "menor" else (dif > 0)
        lectura = f"{_n(abs(dif), 0)} puntos {'por debajo' if dif < 0 else 'por encima'}" if round(dif) else "Prácticamente igual"
        filas.append(f"""
        <tr>
          <th scope="row">{_e(titulo)}</th>
          <td class="num">{_n(med, 0)}%</td>
          <td class="num">{ref:.0f}%</td>
          <td>{lectura}</td>
          <td>{hip}: {sentido} que Meta</td>
          <td class="{'va' if va else 'no-va'}">{'Sí' if va else 'No'}</td>
        </tr>""")
    if not filas:
        return ""
    return f"""
  <section id="comparacion">
    <h2>Comparación con Meta</h2>
    <p>Meta publicó en 2024 que, de cada 100 pruebas que escribió su herramienta, 75 se pudieron ejecutar,
    57 pasaron siempre y 25 sirvieron. Esos proyectos ya tenían pruebas escritas por personas.
    Acá se mide lo mismo en proyectos que no tienen ninguna.</p>
    <div class="tabla-scroll">
    <table>
      <thead><tr><th>Etapa</th><th class="num">Acá (proyecto típico)</th><th class="num">Meta 2024</th>
        <th>Diferencia</th><th>Lo que esperábamos</th><th>¿Va en esa dirección?</th></tr></thead>
      <tbody>{''.join(filas)}</tbody>
    </table>
    </div>
    <p class="nota">«Proyecto típico» es la mediana entre {n} proyecto{'s' if n != 1 else ''}.
    {'Con tan pocos proyectos esto es una primera mirada, no una conclusión: ' if n < 10 else ''}la
    comparación con prueba estadística (Wilcoxon) se hace sobre los 15 a 20 proyectos del experimento completo.</p>
  </section>"""


def _ejemplos(repos):
    todas = [p for r in repos for p in r["pruebas"]]
    if not todas:
        return ""
    sin_asercion = [p for p in todas if p["aporta"] and not p["tiene_asercion"]]
    trampa = sin_asercion[0] if sin_asercion else None
    mostrados = {_limpio(trampa["codigo_prueba"])} if trampa else set()
    bloques = []
    for cat, (titulo, explicacion) in CATEGORIAS.items():
        candidatas = [p for p in todas if p["categoria"] == cat]
        if not candidatas:
            continue
        # Preferir una que compruebe algo y que no repita el código de otro ejemplo
        orden = sorted(candidatas, key=lambda p: (_limpio(p["codigo_prueba"]) in mostrados, -p["tiene_asercion"]))
        p = orden[0]
        mostrados.add(_limpio(p["codigo_prueba"]))
        imports = (p["imports"] or "").strip().strip('"').strip()
        codigo = (imports + "\n\n" if imports else "") + _limpio(p["codigo_prueba"])
        motivo = ""
        if cat in ("falla", "no_compila"):
            lineas = [l.strip() for l in (p["salida_error"] or "").splitlines() if "Error" in l or "assert" in l]
            if lineas:
                motivo = f'<p class="motivo">Lo que respondió Python: <code>{_e(lineas[-1][:160])}</code></p>'
        bloques.append(f"""
      <figure class="ejemplo cat-{cat}">
        <figcaption><strong>{_e(titulo)}</strong> <span class="cuantas">{len(candidatas)} en total</span>
          <span class="explica">{_e(explicacion)}</span></figcaption>
        <pre><code>{_e(codigo)}</code></pre>
        {motivo}
      </figure>""")
    extra = ""
    if trampa:
        p = trampa
        extra = f"""
      <figure class="ejemplo cat-trampa">
        <figcaption><strong>Pasó el filtro, pero no verifica nada</strong>
          <span class="cuantas">{len(sin_asercion)} en total</span>
          <span class="explica">Ejecuta el código y suma cobertura, pero nunca compara el resultado con lo
          esperado: si la función estuviera rota, esta prueba igual pasaría. Por eso el estudio no se queda
          solo con la cobertura y cuenta aparte las pruebas que comprueban algo.</span></figcaption>
        <pre><code>{_e(_limpio(p['codigo_prueba']))}</code></pre>
      </figure>"""
    return f"""
  <section id="ejemplos">
    <h2>Ejemplos reales de esta ejecución</h2>
    <p>Una prueba de cada tipo, tal como la escribió la IA.</p>
    {''.join(bloques)}{extra}
  </section>"""


def _limpio(codigo):
    """Quita la sangría común (las pruebas pueden venir indentadas como métodos de una clase)."""
    return textwrap.dedent((codigo or "").strip("\n")).strip()


def _detalles_tecnicos(ej, cfg, repos):
    h = cfg.get("herramienta", {})
    tokens = sum((c["tokens_entrada"] or 0) + (c["tokens_salida"] or 0) for r in repos for c in r["corridas"])
    costo = sum(c["costo_usd"] or 0 for r in repos for c in r["corridas"])
    dur = sum(c["duracion_seg"] or 0 for r in repos for c in r["corridas"])
    try:
        maquina = json.loads(ej["maquina"] or "{}")
    except ValueError:
        maquina = {}
    filas = [
        ("Modelo de lenguaje", ej["modelo"]),
        ("Herramienta evaluada", f"Qodo Cover (cover-agent), commit {str(h.get('qodo_cover_commit', ''))[:10]}"),
        ("Rondas por archivo", h.get("max_iteraciones")),
        ("Repeticiones por prueba (estabilidad)", h.get("ejecuciones_por_prueba")),
        ("Corridas por proyecto", cfg.get("experimento", {}).get("corridas_por_repo")),
        ("Tiempo total de proceso", f"{_n(dur / 60)} minutos"),
        ("Consumo del modelo", f"{tokens:,} tokens".replace(",", ".")),
        ("Costo estimado", f"US$ {_n(costo, 4)}"),
        ("Entorno", "sin contenedor (modo depuración)" if ej["imagen_docker"] == "sin-docker"
                    else f"contenedor Docker {str(ej['imagen_docker'])[:19]}"),
        ("Máquina", f"{maquina.get('sistema', '')}, Python {maquina.get('python', '')}"),
        ("Versión del pipeline", ej["commit_pipeline"]),
        ("Huella de la configuración", ej["huella_config"]),
    ]
    tabla = "".join(f"<tr><th scope='row'>{_e(a)}</th><td>{_e(b)}</td></tr>" for a, b in filas)
    return f"""
  <section id="tecnico">
    <details>
      <summary>Detalles técnicos de la ejecución</summary>
      <p>Lo necesario para que otra persona repita exactamente esta ejecución (reproducibilidad, RNF-01).</p>
      <div class="tabla-scroll"><table class="tecnica">{tabla}</table></div>
    </details>
  </section>"""


# -----------------------------------------------------------------------------------------------
# Página completa
# -----------------------------------------------------------------------------------------------
def generar_html(almacen, ejecucion_id=None, destino=None):
    if ejecucion_id is None:
        fila = almacen.consultar("SELECT MAX(id) AS id FROM ejecuciones")
        ejecucion_id = fila[0]["id"] if fila else None
    if not ejecucion_id:
        return None
    ej, repos = _datos(almacen, ejecucion_id)
    try:
        cfg = json.loads(ej["config_json"] or "{}")
    except ValueError:
        cfg = {}
    n_corridas = cfg.get("experimento", {}).get("corridas_por_repo", 1) or 1
    escritas = sum(r["conteo"]["escritas"] for r in repos)
    sirvieron = sum(r["conteo"]["aporta"] for r in repos)
    n_repos = len(repos)
    nombre_exp = {"prueba": "Prueba rápida", "piloto": "Prueba piloto"}.get(ej["experimento"], ej["experimento"])

    modelo_txt = ej["modelo"]

    tarjetas = "".join(_tarjeta_repo(r, n_corridas) for r in repos)
    leyenda_meta = ('<p class="leyenda"><span class="meta-muestra" aria-hidden="true"></span> '
                    'La línea punteada marca dónde quedó Meta en esa misma etapa.</p>')

    pagina = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Informe — {_e(nombre_exp)} (ejecución {ej['id']})</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <header>
    <p class="contexto">Tesis FP-UNA · Generación automática de pruebas con IA en proyectos sin pruebas</p>
    <h1>{_e(nombre_exp)}</h1>
    <p class="datos-ejecucion">Ejecución {ej['id']}, del {_e(ej['inicio'])}. Modelo: {_e(modelo_txt)}.
      {n_repos} proyecto{'s' if n_repos != 1 else ''}.</p>
  </header>
  <section class="titular">
    <p class="frase">La IA escribió <strong>{escritas}</strong> pruebas.<br>
      <strong class="verde">{sirvieron}</strong> sirvieron.</p>
    <p class="bajada">Cada prueba pasa por un filtro de tres etapas. Solo las que superan las tres
    se quedan en el proyecto. Este informe muestra cuántas sobrevivieron a cada etapa.</p>
  </section>

  <section id="pasos">
    <h2>Qué hizo el sistema</h2>
    <ol class="pasos">
      <li><strong>Eligió el proyecto</strong> y verificó que cumpla los criterios del estudio: chico, sin pruebas, instalable.</li>
      <li><strong>Lo preparó</strong> en un entorno aislado, instalando lo que necesita para funcionar.</li>
      <li><strong>Midió cuánto código revisaban sus pruebas</strong> al empezar. Sin pruebas, casi nada.</li>
      <li><strong>Le pidió pruebas a la IA</strong>, archivo por archivo, a través de Qodo Cover: la versión abierta de la herramienta de Meta.</li>
      <li><strong>Clasificó cada prueba</strong> según el filtro y midió cuánto código quedó revisado al final.</li>
    </ol>
  </section>

  <section id="proyectos">
    <h2>El filtro, proyecto por proyecto</h2>
    {leyenda_meta}
    {tarjetas}
  </section>
  {_comparacion(repos)}
  {_ejemplos(repos)}

  <section id="glosario">
    <h2>Cómo leer este informe</h2>
    <dl>
      <dt>Prueba unitaria</dt><dd>Un programita que verifica que una parte del código hace lo que debe.
        Por ejemplo: le pasa 2 y 3 a una función que suma y comprueba que devuelva 5.</dd>
      <dt>Cobertura</dt><dd>Qué porcentaje del código se ejecuta cuando corren las pruebas. Lo que no se
        ejecuta, nadie lo revisó.</dd>
      <dt>El filtro</dt><dd>La idea central del trabajo de Meta: la IA escribe muchas pruebas y el sistema
        descarta automáticamente las que no se pueden ejecutar, las que fallan y las que no suman nada. A una
        persona solo le llega lo que sobrevive.</dd>
      <dt>Por qué compararse con Meta</dt><dd>Meta aplicó ese filtro en Instagram y Facebook, que ya tenían
        pruebas. Este estudio aplica lo mismo en proyectos chicos que no tienen ninguna, que es la situación
        de muchos equipos pequeños, y mira si los números se sostienen.</dd>
    </dl>
  </section>
  {_detalles_tecnicos(ej, cfg, repos)}
</main>
</body>
</html>
"""
    if destino is None:
        destino = RAIZ / cfg.get("experimento", {}).get("carpeta_resultados", "resultados") / ej["experimento"]
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / f"informe_ejecucion_{ej['id']}.html"
    archivo.write_text(pagina, encoding="utf-8")
    (destino / "informe.html").write_text(pagina, encoding="utf-8")  # siempre la última
    return archivo


CSS = """
:root {
  --papel: #f6f8fa; --hoja: #ffffff; --tinta: #16202a; --tenue: #56636f; --linea: #d6dde4;
  --azul: #1f5f8b; --verde: #2b7a4b; --rojo: #a33d2c; --ambar: #7a5200; --ambar-fondo: #fff3cf;
  --meta: #6c7782; --codigo: #eef2f5;
  color-scheme: light;
  box-sizing: border-box;
  padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px);
}
@media (prefers-color-scheme: dark) {
  :root {
    --papel: #10161c; --hoja: #18212a; --tinta: #e4eaf0; --tenue: #9aa8b5; --linea: #2c3a46;
    --azul: #6ea9d4; --verde: #6cc08e; --rojo: #e58a76; --ambar: #f2c45e; --ambar-fondo: #3a2c0a;
    --meta: #a3afba; --codigo: #0f151b;
    color-scheme: dark;
  }
}
*, *::before, *::after { box-sizing: inherit; }
html { scroll-padding-top: env(safe-area-inset-top, 0px); }
body { margin: 0; background: var(--papel); color: var(--tinta);
  font: 17px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 52rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
h1, h2, h3, .frase { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  font-weight: 600; line-height: 1.2; }
h1 { font-size: 2.4rem; margin: .2rem 0 .4rem; }
h2 { font-size: 1.55rem; margin: 3rem 0 .8rem; padding-top: 1rem; border-top: 1px solid var(--linea); }
h3 { font-size: 1.3rem; margin: 0 0 .5rem; }
p { max-width: 68ch; }
.contexto, .datos-ejecucion, .nota, .leyenda { color: var(--tenue); font-size: .92rem; }
.contexto { margin: 0; }
.datos-ejecucion { margin: 0; }


.titular { margin: 2.5rem 0 0; }
.frase { font-size: clamp(2rem, 6vw, 3.1rem); margin: 0 0 .8rem; font-weight: 500; }
.frase strong { font-weight: 700; }
.verde { color: var(--verde); }
.bajada { font-size: 1.05rem; color: var(--tenue); }

.pasos { padding-left: 1.4rem; }
.pasos li { margin: .45rem 0; padding-left: .3rem; }

.repo { background: var(--hoja); border: 1px solid var(--linea); border-radius: 6px; padding: 1.4rem 1.4rem 1rem; margin: 1.2rem 0; }
.resumen-repo { margin: 0 0 1.2rem; }
.embudo { display: grid; gap: .55rem; }
.etapa { display: grid; grid-template-columns: minmax(9rem, 14rem) 1fr 4.6rem; gap: .9rem; align-items: center; }
.etapa-titulo { display: block; font-weight: 600; }
.etapa-ayuda { display: block; font-size: .82rem; color: var(--tenue); line-height: 1.35; }
.etapa-barra { position: relative; height: 2.1rem; background: var(--codigo); border-radius: 3px; }
.relleno { height: 100%; border-radius: 3px; background: var(--azul); }
.etapa-0 .relleno { opacity: .3; } .etapa-1 .relleno { opacity: .55; } .etapa-2 .relleno { opacity: .8; }
.etapa-3 .relleno { background: var(--verde); }
.etapa-valor { text-align: right; font-variant-numeric: tabular-nums; }
.etapa-valor strong { font-size: 1.25rem; }
.etapa-valor span { display: block; font-size: .85rem; color: var(--tenue); }
.meta-marca { position: absolute; top: -4px; bottom: -4px; width: 0; border-left: 2px dashed var(--meta); }
.meta-marca span { position: absolute; top: 100%; left: 3px; font-size: .7rem; color: var(--meta); white-space: nowrap; margin-top: 1px; }
.etapa:has(.meta-marca) { margin-bottom: .55rem; }
.meta-muestra { display: inline-block; width: 0; height: 1em; border-left: 2px dashed var(--meta); vertical-align: middle; margin: 0 .3rem; }
.nota-asercion { margin: 1rem 0 0; font-size: .95rem; }

.cobertura { margin-top: 1.3rem; padding-top: 1rem; border-top: 1px dashed var(--linea); }
.cob-titulo { margin: 0 0 .5rem; font-weight: 600; }
.cob-fila { display: grid; grid-template-columns: 9rem 1fr 4.2rem; gap: .9rem; align-items: center; margin: .3rem 0; }
.cob-fila strong { text-align: right; font-variant-numeric: tabular-nums; }
.cob-barra { height: .85rem; background: var(--codigo); border-radius: 3px; }
.cob-barra div { height: 100%; background: var(--meta); border-radius: 3px; }
.cob-barra.despues div { background: var(--verde); }

.criterios { margin-top: 1rem; font-size: .93rem; }
.criterios summary, #tecnico summary { cursor: pointer; color: var(--azul); font-weight: 600; }
.criterios ul { list-style: none; padding: 0; margin: .6rem 0 0; }
.criterios li { display: flex; gap: .6rem; margin: .4rem 0; }
.criterios small { display: block; color: var(--tenue); }
.tilde { font-weight: 700; width: 1rem; flex: none; }
.si .tilde { color: var(--verde); } .no .tilde { color: var(--rojo); }
.aviso-estado { color: var(--rojo); font-weight: 600; }
.vacio { color: var(--tenue); }

.tabla-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .95rem; }
th, td { text-align: left; padding: .55rem .6rem; border-bottom: 1px solid var(--linea); vertical-align: top; }
thead th { font-size: .85rem; color: var(--tenue); font-weight: 600; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.va { color: var(--verde); font-weight: 700; } .no-va { color: var(--rojo); font-weight: 700; }
.tecnica th { width: 40%; font-weight: 500; color: var(--tenue); }

.ejemplo { margin: 1.4rem 0; padding: 0 0 0 1rem; border-left: 4px solid var(--linea); }
.cat-aceptada { border-color: var(--verde); } .cat-no_compila, .cat-falla { border-color: var(--rojo); }
.cat-sin_aporte { border-color: var(--meta); } .cat-trampa { border-color: var(--ambar); }
.ejemplo figcaption strong { font-size: 1.05rem; }
.cuantas { color: var(--tenue); font-size: .85rem; margin-left: .4rem; }
.explica { display: block; color: var(--tenue); font-size: .93rem; margin: .2rem 0 .5rem; max-width: 68ch; }
pre { background: var(--codigo); padding: .9rem 1rem; border-radius: 4px; overflow-x: auto; font-size: .85rem; line-height: 1.45; margin: 0; }
code { font-family: ui-monospace, "Cascadia Code", Consolas, "Liberation Mono", monospace; }
.motivo { font-size: .88rem; margin: .5rem 0 0; }
.motivo code { background: var(--codigo); padding: .1rem .3rem; border-radius: 3px; word-break: break-word; }

dl dt { font-weight: 700; margin-top: 1rem; }
dl dd { margin: .2rem 0 0; max-width: 68ch; }

@media (max-width: 640px) {
  .etapa { grid-template-columns: 1fr 4rem; }
  .etapa-texto { grid-column: 1 / -1; }
  h1 { font-size: 2rem; }
}
@media print {
  body { background: #fff; color: #000; font-size: 12pt; }
  .repo { break-inside: avoid; }
}
"""
