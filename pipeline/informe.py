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

# Los tres controles por los que pasa cada prueba, en lenguaje simple (pedido de la tutora: que
# alguien sin formación técnica entienda cada número). Antes la última etapa se llamaba «Sirvieron».
ETAPAS = [  # (clave, título, explicación)
    ("escritas", "Escritas por la IA", "Todas las pruebas que propuso la IA, buenas y malas."),
    ("compila", "Control 1 · Arrancan", "Python las puede ejecutar: no tienen errores de escritura."),
    ("pasa", "Control 2 · Dan bien las 5 veces", "Se ejecutaron 5 veces y en las 5 dieron el resultado esperado."),
    ("aporta", "Control 3 · Revisan algo nuevo = APROBADAS",
     "Revisan una parte del programa que ninguna otra prueba revisaba. Son las que se guardan."),
]

CATEGORIAS = {
    "aceptada": ("Aprobada", "Superó los 3 controles: arranca, da bien las 5 veces y revisa algo que antes "
                             "nadie revisaba. Es la única que queda guardada en el proyecto."),
    "sin_aporte": ("Correcta, pero repetida", "Superó los controles 1 y 2, pero revisa lo mismo que otra prueba "
                                              "ya revisaba (falló el control 3). No suma nada y se descarta."),
    "falla": ("Da un resultado incorrecto", "Arranca, pero el resultado no es el que la propia prueba esperaba, "
                                            "o cambia de una vez a otra (falló el control 2). Se descarta."),
    "no_compila": ("No arranca", "Tiene un error de escritura o usa algo que no existe: Python ni siquiera la "
                                 "puede ejecutar (falló el control 1). Se descarta."),
    "error": ("Sin evaluar", "La herramienta no llegó a evaluarla."),
}

ESTADOS = {
    "completo": "Procesado completo",
    "no_preparable": "No se pudo preparar: falló la instalación de lo que el programa necesita. "
                     "Queda registrado y se sigue con el siguiente; a cuántos programas les pasa es parte del estudio.",
    "tiempo_agotado": "Se cortó por superar el tiempo máximo por programa.",
    "error_contenedor": "El entorno aislado terminó con un error antes de dejar resultados.",
    "error_cobertura_inicial": "No se pudo medir la cobertura inicial.",
    "error_llm": "La IA no respondió (cuota agotada, servicio saturado, clave o modelo inválidos). "
                 "Esta corrida no es un dato válido y hay que repetirla.",
}

CRITERIOS = {
    "tamaño": "Es un programa chico (500 a 5.000 líneas)",
    "sin_pruebas_previas": "No tiene pruebas",
    "dependencias_declaradas": "Se puede instalar de forma automática",
    "licencia": "Su licencia permite usarlo para investigar",
    "actividad": "Tiene al menos un año de historia",
    "revision_manual": "No necesita servicios externos ni contraseñas (revisión manual)",
    "descarga": "Se pudo descargar",
}

# Hipótesis del anteproyecto: qué se esperaba de cada control respecto de Meta
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
def _barra_controles(cant, total, pct, ref, titulo, clase=""):
    """Una fila del gráfico de controles: barra proporcional + marca punteada de Meta."""
    ancho = 100 * cant / total if total else 0
    marca = ""
    if ref is not None:
        marca = (f'<span class="meta-marca" style="left:{ref}%" aria-hidden="true">'
                 f'<span>Meta {ref:.0f}%</span></span>')
    tip = f"{titulo}: {cant} de {total} pruebas ({_n(pct, 0)}%)" + (f". Meta: {ref:.0f}%" if ref is not None else "")
    return f"""
          <div class="etapa-barra" role="img" aria-label="{_e(tip)}" title="{_e(tip)}">
            <div class="relleno {clase}" style="width:{max(ancho, 0.6):.1f}%"></div>
            {marca}
          </div>"""


def _filas_controles(conteo, pcts, total):
    filas = []
    for i, (clave, titulo, ayuda) in enumerate(ETAPAS):
        cant = conteo[clave]
        pct = 100.0 if clave == "escritas" else pcts.get(clave)
        ref = REFERENCIA_META.get(clave)
        clase = "final" if clave == "aporta" else ""
        filas.append(f"""
        <div class="etapa etapa-{i}">
          <div class="etapa-texto">
            <span class="etapa-titulo">{_e(titulo)}</span>
            <span class="etapa-ayuda">{_e(ayuda)}</span>
          </div>{_barra_controles(cant, total, pct, ref, titulo, clase)}
          <div class="etapa-valor"><strong>{cant}</strong> <span>{'de ' + str(total) if clave == 'escritas' else _n(pct, 0) + '%'}</span></div>
        </div>""")
    return "\n".join(filas)


def _grafico_controles(repos):
    """GRÁFICO 1: todas las pruebas de la ejecución juntas, control por control, contra Meta."""
    conteo = {k: sum(r["conteo"][k] for r in repos) for k, _, _ in ETAPAS}
    total = conteo["escritas"]
    if not total:
        return ""
    pcts = {k: _pct(conteo[k], total) for k in ("compila", "pasa", "aporta")}
    aprob = pcts["aporta"]
    return f"""
  <section id="grafico-controles" class="grafico">
    <h2>1. De las pruebas que escribió la IA, ¿cuántas quedaron aprobadas?</h2>
    <p class="como-leer"><strong>Cómo leerlo:</strong> cada barra muestra cuántas pruebas siguen en pie después
    de cada control. La línea punteada marca dónde quedó Meta (Facebook) en ese mismo control.</p>
    <div class="embudo">{_filas_controles(conteo, pcts, total)}</div>
    <p class="lectura">En palabras simples: <strong>de cada 100 pruebas que escribió la IA, {_n(aprob, 0)} quedaron
    aprobadas</strong>. En el estudio de Meta fueron {REFERENCIA_META['aporta']:.0f} de cada 100.</p>
  </section>"""


def _que_es_aprobada():
    return """
  <section id="aprobada" class="recuadro">
    <h2>¿Qué es una prueba «aprobada»?</h2>
    <p>Una <strong>prueba</strong> es un programita que revisa si otra parte del programa funciona bien. Por
    ejemplo: le da 2 y 3 a la función que suma y verifica que devuelva 5. Si algún día alguien rompe esa
    función, la prueba lo detecta.</p>
    <p>La IA escribe muchas pruebas, pero no todas sirven. Por eso cada una pasa por <strong>tres controles
    automáticos</strong>, como en una línea de control de calidad:</p>
    <ol class="controles">
      <li><strong>¿Arranca?</strong> Python tiene que poder ejecutarla. Si tiene un error de escritura, se descarta.</li>
      <li><strong>¿Da bien siempre?</strong> Se ejecuta 5 veces y en las 5 tiene que dar el resultado que ella
        misma espera. Si falla o cambia de una vez a otra, no es confiable y se descarta.</li>
      <li><strong>¿Revisa algo nuevo?</strong> Tiene que revisar una parte del programa que ninguna otra prueba
        revisaba. Si repite lo que ya estaba cubierto, no suma y se descarta.</li>
    </ol>
    <p class="destacado">Una prueba <strong>aprobada</strong> es la que superó los tres controles. Son las únicas
    que se guardan en el proyecto; las demás se descartan solas, sin que intervenga una persona.</p>
  </section>"""


def _grafico_cobertura(repos):
    """GRÁFICO 2: qué parte de cada programa quedó revisada, separando lo que aporta el solo import."""
    filas = []
    for r in repos:
        fin = r.get("cob_fin")
        if fin is None:
            continue
        sem = r.get("cob_sem")
        base = sem if sem is not None else 0.0
        ia = max(fin - base, 0.0)
        tip = (f"{r['nombre']}: {_n(base)}% solo con abrir los archivos + {_n(ia)} puntos de la IA = {_n(fin)}% revisado")
        seg_base = (f'<div class="seg seg-base" style="width:{base:.1f}%"><span>{_n(base, 0)}%</span></div>'
                    if base >= 0.5 else "")
        seg_ia = f'<div class="seg seg-ia" style="width:{ia:.1f}%"><span>IA +{_n(ia, 0)}</span></div>' if ia >= 0.5 else ""
        filas.append(f"""
      <div class="cob-proy">
        <span class="cob-nombre">{_e(r['nombre'])}</span>
        <div class="cob-pista" role="img" aria-label="{_e(tip)}" title="{_e(tip)}">{seg_base}{seg_ia}</div>
        <span class="cob-total"><strong>{_n(fin)}%</strong></span>
      </div>""")
    if not filas:
        return ""
    hay_base = any(r.get("cob_sem") is not None for r in repos)
    nota_base = ("" if hay_base else
                 '<p class="nota">Esta ejecución no midió la parte que se revisa solo con abrir los archivos: '
                 'todo el porcentaje aparece como aporte de la IA y puede estar sobreestimado.</p>')
    return f"""
  <section id="grafico-cobertura" class="grafico">
    <h2>2. ¿Qué parte de cada programa quedó revisada?</h2>
    <p class="como-leer"><strong>Cómo leerlo:</strong> cada barra es un programa completo (100%). Al empezar,
    ninguno tenía pruebas: 0% revisado. El color muestra qué parte quedó revisada al final y gracias a qué.</p>
    <div class="cob-leyenda">
      <span><i class="muestra seg-base"></i> Se revisa solo con abrir los archivos (no es mérito de la IA)</span>
      <span><i class="muestra seg-ia"></i> Lo que agregaron las pruebas aprobadas de la IA</span>
      <span><i class="muestra vacia"></i> Quedó sin revisar</span>
    </div>
    <div class="cob-grafico">{''.join(filas)}
      <div class="cob-escala" aria-hidden="true"><span></span><div><span>0%</span><span>50%</span><span>100%</span></div><span></span></div>
    </div>
    <p class="lectura">Por qué separamos el primer tramo: al abrir un archivo, Python ya recorre una parte (la
    lista de funciones, los valores fijos). Eso cuenta como «revisado» aunque no haya ninguna prueba, así que no
    se le puede atribuir a la IA. <strong>El aporte real de la IA es solo el segundo tramo.</strong></p>
    {nota_base}
  </section>"""


def _embudo(r):
    return _filas_controles(r["conteo"], r["pct"], r["conteo"]["escritas"])


def _cobertura(r):
    fin = r.get("cob_fin")
    if fin is None:
        return ""
    sem = r.get("cob_sem")
    if sem is None:
        return f'<p class="cob-texto">Parte del programa revisada al final: <strong>{_n(fin)}%</strong>.</p>'
    return (f'<p class="cob-texto">Parte del programa revisada: <strong>{_n(sem)}%</strong> solo con abrir los '
            f'archivos → <strong>{_n(fin)}%</strong> con las pruebas aprobadas. '
            f'Aporte de la IA: <strong>+{_n(fin - sem)} puntos</strong>.</p>')


def _criterios(r):
    if not r.get("criterios"):
        return ""
    items = "".join(
        f'<li class="{"si" if c["cumple"] else "no"}"><span class="tilde" aria-hidden="true">{"✓" if c["cumple"] else "✗"}</span>'
        f'<span>{_e(CRITERIOS.get(c["criterio"], c["criterio"]))}<small>{_e(c["detalle"])}</small></span></li>'
        for c in r["criterios"])
    return f"""
      <details class="criterios">
        <summary>Por qué este programa entró al estudio</summary>
        <ul>{items}</ul>
      </details>"""


def _tarjeta_repo(r, n_corridas):
    c = r["conteo"]
    errores = [e for e in r["estados"] if e != "completo"]
    aviso = ""
    if errores:
        aviso = "".join(f'<p class="aviso-estado">{_e(ESTADOS.get(e, e))}</p>' for e in sorted(set(errores)))
    if c["escritas"] == 0:
        cuerpo = '<p class="vacio">No hubo pruebas para clasificar en este programa.</p>'
    else:
        verificacion = ""
        if c["aporta"]:
            sin = c["aporta"] - r["aceptadas_con_asercion"]
            verificacion = (f'<p class="nota-asercion">De las {c["aporta"]} aprobadas, '
                            f'<strong>{r["aceptadas_con_asercion"]}</strong> comparan el resultado con lo esperado'
                            + (f' y <strong>{sin}</strong> solo ejecutan código sin comparar nada '
                               f'(suman revisión, pero no detectarían un error).' if sin else '.')
                            + '</p>')
        nota_corridas = (f'<p class="nota">Cantidades: suma de las {n_corridas} corridas. '
                         f'Porcentajes: promedio de las corridas.</p>' if n_corridas > 1 else "")
        cuerpo = f"""
      <div class="embudo">{_embudo(r)}</div>
      {_cobertura(r)}{verificacion}
      <p class="resumen-repo">En total: la IA escribió <strong>{c['escritas']}</strong> pruebas para este programa y
        <strong>{c['aporta']}</strong> quedaron aprobadas.</p>{nota_corridas}"""
    return f"""
    <article class="repo">
      <h3>{_e(r['nombre'])}</h3>
      {aviso}{cuerpo}{_criterios(r)}
    </article>"""


def _comparacion(repos):
    con_datos = [r for r in repos if r["conteo"]["escritas"]]
    if not con_datos:
        return ""
    cab = "".join(f'<th class="num">{_e(r["nombre"])}</th>' for r in con_datos)
    filas = []
    for clave, titulo, _ in ETAPAS[1:]:
        vals = [r["pct"].get(clave) for r in con_datos]
        ref = REFERENCIA_META[clave]
        sentido, hip = HIPOTESIS[clave]
        validos = [v for v in vals if v is not None]
        if not validos:
            continue
        med = st.median(validos)
        va = (med < ref) if sentido == "menor" else (med > ref)
        celdas = "".join(f'<td class="num">{_n(v, 0)}%</td>' for v in vals)
        resultado = (f"Salió {'más bajo' if med < ref else 'más alto'} que Meta: "
                     f"{'coincide con lo esperado' if va else 'no coincide con lo esperado'}")
        filas.append(f"""
        <tr>
          <th scope="row">{_e(titulo.replace(' = APROBADAS', ''))}</th>{celdas}
          <td class="num">{ref:.0f}%</td>
          <td>{'Menos' if sentido == 'menor' else 'Más'} que Meta ({hip})</td>
          <td class="{'va' if va else 'no-va'}">{resultado}</td>
        </tr>""")
    n = len(con_datos)
    return f"""
  <section id="comparacion">
    <h2>3. Comparación con Meta, programa por programa</h2>
    <p>En 2024 Meta (la empresa de Facebook e Instagram) publicó que, de cada 100 pruebas que escribió su
    herramienta, 75 arrancaron, 57 dieron bien siempre y 25 quedaron aprobadas. Sus programas ya tenían pruebas
    escritas por personas; acá se mide lo mismo en programas que no tienen ninguna.</p>
    <div class="tabla-scroll">
    <table>
      <thead><tr><th>Control</th>{cab}<th class="num">Meta 2024</th>
        <th>Lo que esperábamos (hipótesis)</th><th>Resultado</th></tr></thead>
      <tbody>{''.join(filas)}</tbody>
    </table>
    </div>
    <p class="nota">Los porcentajes son sobre todas las pruebas que escribió la IA en cada programa. El
    «Resultado» usa el programa del medio (mediana) de los {n}.
    {'Con tan pocos programas esto es una primera mirada, no una conclusión: ' if n < 10 else ''}la
    comparación con prueba estadística (Wilcoxon) se hace sobre los 15 a 20 programas del experimento completo.</p>
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
                motivo = f'<p class="motivo">El error que mostró Python: <code>{_e(lineas[-1][:160])}</code></p>'
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
        <figcaption><strong>Aprobada, pero no compara nada</strong>
          <span class="cuantas">{len(sin_asercion)} en total</span>
          <span class="explica">Superó los tres controles, pero solo pone a andar el código y nunca compara el
          resultado con lo esperado: si la función estuviera rota, esta prueba igual daría bien. Por eso el estudio
          cuenta aparte las aprobadas que de verdad comparan algo.</span></figcaption>
        <pre><code>{_e(_limpio(p['codigo_prueba']))}</code></pre>
      </figure>"""
    return f"""
  <section id="ejemplos">
    <h2>6. Ejemplos reales de esta ejecución</h2>
    <p>Una prueba de cada tipo, tal como la escribió la IA. No hace falta leer el código: el título y la
    explicación de cada una dicen por qué se aprobó o se descartó.</p>
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
    aprobadas = sum(r["conteo"]["aporta"] for r in repos)
    n_repos = len(repos)
    nombre_exp = {"prueba": "Prueba rápida", "piloto": "Prueba piloto"}.get(ej["experimento"], ej["experimento"])
    nombres = ", ".join(r["nombre"] for r in repos)
    tarjetas = "".join(_tarjeta_repo(r, n_corridas) for r in repos)
    sem_vals = [r["cob_sem"] for r in repos if r.get("cob_sem") is not None and r.get("cob_fin") is not None]
    aporte_vals = [r["cob_fin"] - r["cob_sem"] for r in repos
                   if r.get("cob_sem") is not None and r.get("cob_fin") is not None]
    linea_aporte = (f"<li>La parte de cada programa revisada gracias a la IA subió entre "
                    f"<strong>{_n(min(aporte_vals), 0)} y {_n(max(aporte_vals), 0)} puntos</strong> "
                    f"(sin contar lo que se revisa solo con abrir los archivos).</li>" if aporte_vals else "")

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
    <p class="contexto">Tesis FP-UNA · Generación automática de pruebas con IA en programas sin pruebas</p>
    <h1>{_e(nombre_exp)}</h1>
    <p class="entrada">Le pedimos a una IA ({_e(ej['modelo'].split('/')[-1])}) que escriba pruebas para
      {n_repos} programa{'s' if n_repos != 1 else ''} real{'es' if n_repos != 1 else ''} que no tenía{'n' if n_repos != 1 else ''}
      ninguna ({_e(nombres)}), y medimos cuántas de esas pruebas valen la pena.</p>
    <p class="datos-ejecucion">Ejecución {ej['id']} · {_e(ej['inicio'])}</p>
  </header>
  {_grafico_controles(repos)}
  {_que_es_aprobada()}
  {_grafico_cobertura(repos)}
  {_comparacion(repos)}

  <section id="resumen" class="recuadro">
    <h2>En resumen</h2>
    <ul>
      <li>La IA escribió <strong>{escritas}</strong> pruebas en total y <strong>{aprobadas}</strong> quedaron
        aprobadas (superaron los tres controles).</li>
      {linea_aporte}
      <li>Todo el proceso fue automático: elegir los programas, prepararlos, pedirle las pruebas a la IA,
        aplicar los controles y medir.</li>
    </ul>
  </section>

  <section id="proyectos">
    <h2>4. Programa por programa</h2>
    <p class="leyenda">Los mismos controles del gráfico 1, para cada programa por separado. La línea
      punteada (<span class="meta-muestra" aria-hidden="true"></span>) marca dónde quedó Meta.</p>
    {tarjetas}
  </section>

  <section id="pasos">
    <h2>5. Cómo se obtuvieron estos números</h2>
    <ol class="pasos">
      <li><strong>Eligió los programas</strong> y verificó que cumplan los criterios del estudio: chicos, sin pruebas,
        instalables de forma automática.</li>
      <li><strong>Los preparó</strong> cada uno en un entorno aislado (un contenedor), instalando lo que necesitan.</li>
      <li><strong>Midió la parte revisada al empezar</strong>: 0%, porque no tenían pruebas. Y midió también cuánto
        se revisa solo con abrir los archivos, para no atribuírselo a la IA.</li>
      <li><strong>Le pidió pruebas a la IA</strong>, archivo por archivo, a través de Qodo Cover: la versión
        abierta de la herramienta que usó Meta.</li>
      <li><strong>Aplicó los tres controles</strong> a cada prueba y midió la parte revisada al final.</li>
    </ol>
  </section>
  {_ejemplos(repos)}

  <section id="glosario">
    <h2>7. Palabras que aparecen en el informe</h2>
    <dl>
      <dt>Prueba (prueba unitaria)</dt><dd>Un programita que verifica que una parte del programa hace lo que
        debe. Por ejemplo: le da 2 y 3 a una función que suma y comprueba que devuelva 5.</dd>
      <dt>Aprobada</dt><dd>Prueba que superó los tres controles (arranca, da bien las 5 veces y revisa algo
        nuevo). Es la única que se guarda.</dd>
      <dt>Parte revisada (cobertura)</dt><dd>Qué porcentaje del programa se pone a andar cuando corren las
        pruebas. Lo que nunca se pone a andar, nadie lo revisó.</dd>
      <dt>Abrir los archivos (importar)</dt><dd>Lo que hace Python antes de usar un archivo: lo lee de arriba
        abajo. Solo con eso ya «recorre» una parte, y por eso se mide aparte.</dd>
      <dt>Meta</dt><dd>La empresa de Facebook e Instagram. En 2024 publicó los resultados de su herramienta
        de pruebas con IA; este estudio repite esa medición en programas chicos que no tienen ninguna prueba,
        que es la situación de muchos equipos pequeños.</dd>
      <dt>Qodo Cover</dt><dd>La herramienta de código abierto que reproduce el método de Meta. Es la que le pide
        las pruebas a la IA y aplica los controles.</dd>
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
  --serie-1: #2a78d6; --serie-2: #eb6834; --neutro: #8a96a3;
  color-scheme: light;
  box-sizing: border-box;
  padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px);
}
@media (prefers-color-scheme: dark) {
  :root {
    --papel: #10161c; --hoja: #18212a; --tinta: #e4eaf0; --tenue: #9aa8b5; --linea: #2c3a46;
    --azul: #6ea9d4; --verde: #6cc08e; --rojo: #e58a76; --ambar: #f2c45e; --ambar-fondo: #3a2c0a;
    --meta: #a3afba; --codigo: #0f151b;
    --serie-1: #3987e5; --serie-2: #d95926; --neutro: #5d6975;
    color-scheme: dark;
  }
}
*, *::before, *::after { box-sizing: inherit; }
html { scroll-padding-top: env(safe-area-inset-top, 0px); }
body { margin: 0; background: var(--papel); color: var(--tinta);
  font: 17px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 52rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
h1, h2, h3 { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  font-weight: 600; line-height: 1.2; }
h1 { font-size: 2.4rem; margin: .2rem 0 .4rem; }
h2 { font-size: 1.55rem; margin: 3rem 0 .8rem; padding-top: 1rem; border-top: 1px solid var(--linea); }
h3 { font-size: 1.3rem; margin: 0 0 .5rem; }
p { max-width: 68ch; }
.contexto, .datos-ejecucion, .nota, .leyenda { color: var(--tenue); font-size: .92rem; }
.contexto { margin: 0; }
.datos-ejecucion { margin: 0; }

.entrada { font-size: 1.15rem; margin: .6rem 0 .3rem; }

.grafico h2, .recuadro h2 { margin-top: 2.2rem; }
.como-leer { color: var(--tenue); font-size: .95rem; margin: 0 0 1.1rem; }
.lectura { margin: 1.4rem 0 0; font-size: 1.05rem; }
.recuadro { background: var(--hoja); border: 1px solid var(--linea); border-radius: 6px; padding: .2rem 1.4rem 1rem; margin: 2rem 0; }
.recuadro h2 { border-top: none; padding-top: 0; margin-top: 1.2rem; }
.controles li { margin: .5rem 0; }
.destacado { border-left: 4px solid var(--serie-2); padding-left: .8rem; }
.recuadro ul { padding-left: 1.3rem; } .recuadro li { margin: .4rem 0; }

.cob-leyenda { display: flex; flex-wrap: wrap; gap: .4rem 1.4rem; font-size: .9rem; color: var(--tenue); margin: 0 0 1rem; }
.cob-leyenda span { display: inline-flex; align-items: center; gap: .45rem; }
.muestra { display: inline-block; width: .9rem; height: .9rem; border-radius: 3px; flex: none; }
.muestra.vacia { background: var(--codigo); border: 1px solid var(--linea); }
.cob-grafico { display: grid; gap: .7rem; }
.cob-proy, .cob-escala { display: grid; grid-template-columns: 8rem 1fr 4.5rem; gap: .9rem; align-items: center; }
.cob-nombre { font-weight: 600; }
.cob-pista { display: flex; gap: 2px; height: 2.1rem; background: var(--codigo); border-radius: 4px; overflow: hidden; }
.seg { height: 100%; display: flex; align-items: center; justify-content: center; min-width: 0; }
.seg span { color: #fff; font-size: .85rem; font-weight: 600; white-space: nowrap; overflow: hidden; }
.seg-base { background: var(--serie-1); } .seg-ia { background: var(--serie-2); }
.cob-total { text-align: right; font-variant-numeric: tabular-nums; font-size: 1.15rem; }
.cob-escala > div { display: flex; justify-content: space-between; font-size: .75rem; color: var(--tenue); }
.cob-texto { margin: 1rem 0 0; font-size: .95rem; }

.pasos { padding-left: 1.4rem; }
.pasos li { margin: .45rem 0; padding-left: .3rem; }

.repo { background: var(--hoja); border: 1px solid var(--linea); border-radius: 6px; padding: 1.4rem 1.4rem 1rem; margin: 1.2rem 0; }
.resumen-repo { margin: 1rem 0 0; }
.embudo { display: grid; gap: .55rem; }
.etapa { display: grid; grid-template-columns: minmax(9rem, 14rem) 1fr 4.6rem; gap: .9rem; align-items: center; }
.etapa-titulo { display: block; font-weight: 600; }
.etapa-ayuda { display: block; font-size: .82rem; color: var(--tenue); line-height: 1.35; }
.etapa-barra { position: relative; height: 2.1rem; background: var(--codigo); border-radius: 3px; }
.relleno { height: 100%; border-radius: 3px; background: var(--neutro); }
.relleno.final { background: var(--serie-2); }
.etapa-3 .etapa-titulo { color: var(--tinta); }
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
  .cob-proy, .cob-escala { grid-template-columns: 1fr 4rem; }
  .cob-nombre { grid-column: 1 / -1; } .cob-escala > span:first-child { display: none; }
  .etapa-texto { grid-column: 1 / -1; }
  h1 { font-size: 2rem; }
}
@media print {
  body { background: #fff; color: #000; font-size: 12pt; }
  .repo { break-inside: avoid; }
}
"""
