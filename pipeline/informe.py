"""
INFORME DE EJECUCIÓN (página HTML autocontenida).

Presenta los resultados de una ejecución del pipeline en un registro formal y comprensible para un
lector sin formación técnica: proporción de pruebas generadas que supera cada criterio de filtrado, por
grupo y por proyecto; cobertura de código alcanzada; las dos comparaciones del diseño (grupo B frente al
estudio de referencia y grupo A frente a grupo B) y ejemplos de pruebas generadas. El grupo A no se
contrasta con el estudio de referencia, porque este se realizó sobre proyectos con pruebas preexistentes.

El reporte técnico (tabla de terminal y CSV) sigue disponible: python -m pipeline reporte --tecnico
No usa recursos externos (fuentes, scripts, imágenes): se abre sin conexión.
"""

import html
import json
import statistics as st
import textwrap
from pathlib import Path

from .config import RAIZ
from .reporte import REFERENCIA_META, TIPOS_LLAMADA, consumo as consumo_por_tipo

REFERENCIA = "Alshahwan et al. (2024)"

GRUPOS = {
    "A": ("Grupo A", "Proyectos sin pruebas preexistentes",
          "No contaban con pruebas: el modelo genera la suite de pruebas desde cero."),
    "B": ("Grupo B", "Proyectos con suite de pruebas preexistente",
          "Contaban con pruebas escritas por sus desarrolladores: el modelo amplía la suite existente, "
          "en la misma condición que el estudio de referencia."),
}

# Criterios de filtrado que atraviesa cada prueba generada (sección 2.3 del anteproyecto)
ETAPAS = [  # (clave, título, explicación)
    ("escritas", "Pruebas generadas", "Total de pruebas candidatas propuestas por el modelo."),
    ("compila", "Criterio 1 · Compilan", "Se ejecutan sin errores de sintaxis, de importación ni de nombres no definidos."),
    ("pasa", "Criterio 2 · Son estables", "Pasan en las 5 ejecuciones repetidas."),
    ("aporta", "Criterio 3 · Incrementan la cobertura",
     "Ejecutan líneas del código fuente que ninguna prueba anterior ejecutaba. Son las pruebas aceptadas."),
]

CATEGORIAS = {
    "aceptada": ("Aceptada", "Cumple los tres criterios: compila, es estable e incrementa la cobertura del código "
                             "fuente. Es la única categoría que se incorpora al proyecto."),
    "sin_aporte": ("Válida, sin incremento de cobertura", "Compila y es estable, pero ejecuta líneas ya cubiertas por "
                                                           "otras pruebas (no cumple el criterio 3). Se descarta."),
    "falla": ("Fallida o inestable", "Compila, pero su resultado no coincide con el que la propia prueba espera, o "
                                      "varía entre ejecuciones (no cumple el criterio 2). Se descarta."),
    "no_compila": ("No compila", "Presenta errores de sintaxis o hace referencia a elementos inexistentes "
                                 "(no cumple el criterio 1). Se descarta."),
    "error": ("Sin evaluar", "La herramienta no llegó a evaluarla."),
}

ESTADOS = {
    "completo": "Procesamiento completo",
    "no_preparable": "No fue posible instalar sus dependencias de forma automatizada. Se registra como descarte "
                     "y el proceso continúa con el proyecto siguiente.",
    "tiempo_agotado": "Se interrumpió por superar el tiempo máximo establecido por proyecto.",
    "error_contenedor": "El entorno aislado finalizó con un error antes de registrar resultados.",
    "error_cobertura_inicial": "No fue posible medir la cobertura inicial.",
    "error_llm": "El modelo no respondió (cuota agotada, servicio saturado, clave o modelo inválidos). "
                 "La corrida no constituye un dato válido y debe repetirse.",
    "suite_falla": "Su suite de pruebas preexistente no pasa en el entorno aislado; no es apto para el grupo B.",
    "fuera_de_rango": "Su cobertura inicial está fuera del rango establecido para su grupo; queda excluido.",
    "sin_modulos": "Ningún módulo contaba con un archivo de pruebas preexistente y líneas sin cubrir.",
    "error_entorno": "El entorno aislado no contenía los ajustes del estudio; debe reconstruirse la imagen.",
}

CRITERIOS = {
    "tamaño": "Tamaño entre 500 y 5.000 líneas de código",
    "sin_pruebas_previas": "Sin pruebas preexistentes (grupo A)",
    "con_pruebas_previas": "Con suite de pruebas preexistente (grupo B)",
    "cobertura_inicial": "Cobertura inicial dentro del rango de su grupo",
    "dependencias_declaradas": "Dependencias instalables de forma automatizada",
    "licencia": "Licencia que permite su uso con fines de investigación",
    "actividad": "Al menos un año de actividad registrada",
    "revision_manual": "No requiere servicios externos ni credenciales (revisión manual)",
    "descarga": "Repositorio descargable",
}

# Hipótesis del anteproyecto (sección 2.1): lo esperado del grupo A frente al grupo B
HIPOTESIS_AB = [
    ("compila", "Criterio 1 · Compilan", "H1"),
    ("pasa", "Criterio 2 · Son estables", "H1"),
    ("significativa", "Pruebas aceptadas con verificación significativa", "H2"),
]


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
        r = repos.setdefault(c["repo"], {"nombre": c["repo"], "grupo": c.get("grupo") or "A",
                                         "corridas": [], "pruebas": []})
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
        r["aceptadas_significativas"] = sum(1 for x in p if x["aporta"] and x.get("significativa"))
        # Porcentaje del proyecto = promedio de sus corridas (unidad de análisis: el repositorio, sección 3.4.6)
        r["pct"] = {}
        for k in ("compila", "pasa", "aporta", "significativa"):
            vals = []
            for c in r["corridas"]:
                pc = [x for x in p if x["corrida_id"] == c["id"]]
                if k == "significativa":  # entre las aprobadas
                    pc_ap = [x for x in pc if x["aporta"]]
                    if pc_ap:
                        vals.append(_pct(sum(1 for x in pc_ap if x.get("significativa")), len(pc_ap)))
                elif pc:
                    vals.append(_pct(sum(x[k] for x in pc), len(pc)))
            r["pct"][k] = st.mean(vals) if vals else None
        completas = [c for c in r["corridas"] if c["estado"] == "completo"]
        r["cob_ini"] = st.mean([c["cobertura_inicial"] for c in completas]) if completas else None
        r["cob_fin"] = st.mean([c["cobertura_final"] for c in completas
                                if c["cobertura_final"] is not None]) if completas else None
        base = [c.get("cobertura_base") if c.get("cobertura_base") is not None else c.get("cobertura_semillas")
                for c in completas]
        base = [b for b in base if b is not None]
        r["cob_sem"] = st.mean(base) if base else None  # línea base: A = solo importar; B = suite preexistente
        r["estados"] = [c["estado"] for c in r["corridas"]]
    # Criterios de selección: el registro más reciente de cada (repo, criterio)
    sel = almacen.consultar(
        "SELECT s.* FROM seleccion s JOIN (SELECT repo, criterio, MAX(id) AS mid FROM seleccion "
        "WHERE experimento=? GROUP BY repo, criterio) u ON s.id=u.mid ORDER BY s.id", (ej["experimento"],))
    for s in sel:
        if s["repo"] in repos:
            repos[s["repo"]].setdefault("criterios", []).append(s)
    return ej, sorted(repos.values(), key=lambda r: (r["grupo"], r["nombre"]))


# -----------------------------------------------------------------------------------------------
# Piezas de la página
# -----------------------------------------------------------------------------------------------
def _barra_controles(cant, total, pct, ref, titulo, clase=""):
    """Una fila del gráfico de criterios: barra proporcional + marca punteada del estudio de referencia."""
    ancho = 100 * cant / total if total else 0
    marca = ""
    if ref is not None:
        marca = (f'<span class="meta-marca" style="left:{ref}%" aria-hidden="true">'
                 f'<span>Referencia {ref:.0f}%</span></span>')
    tip = f"{titulo}: {cant} de {total} pruebas ({_n(pct, 0)}%)" + (f". Referencia: {ref:.0f}%" if ref is not None else "")
    return f"""
          <div class="etapa-barra" role="img" aria-label="{_e(tip)}" title="{_e(tip)}">
            <div class="relleno {clase}" style="width:{max(ancho, 0.6):.1f}%"></div>
            {marca}
          </div>"""


def _filas_controles(conteo, pcts, total, con_meta=True):
    filas = []
    for i, (clave, titulo, ayuda) in enumerate(ETAPAS):
        cant = conteo[clave]
        pct = 100.0 if clave == "escritas" else pcts.get(clave)
        ref = REFERENCIA_META.get(clave) if con_meta else None
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


def _conteo(repos):
    return {k: sum(r["conteo"][k] for r in repos) for k, _, _ in ETAPAS}


def _grafico_controles(repos):
    """GRÁFICO 1: embudo de cada grupo. La referencia se marca solo en el grupo B (misma condición)."""
    bloques = []
    for g in ("A", "B"):
        del_grupo = [r for r in repos if r["grupo"] == g]
        conteo = _conteo(del_grupo)
        total = conteo["escritas"]
        if not total:
            continue
        pcts = {k: _pct(conteo[k], total) for k in ("compila", "pasa", "aporta")}
        corto, titulo, ayuda = GRUPOS[g]
        cierre = (f"En el estudio de referencia, ese valor fue del {REFERENCIA_META['aporta']:.0f} %." if g == "B" else
                  "Este grupo no se contrasta con el estudio de referencia, que se realizó sobre proyectos con "
                  "pruebas preexistentes.")
        bloques.append(f"""
    <div class="grupo-bloque grupo-{g}">
      <h3><span class="etiqueta-grupo g{g}">{corto}</span> {_e(titulo)}</h3>
      <p class="como-leer">{_e(ayuda)}</p>
      <div class="embudo">{_filas_controles(conteo, pcts, total, con_meta=(g == "B"))}</div>
      <p class="lectura">Del total de pruebas generadas, el <strong>{_n(pcts['aporta'], 0)} %</strong> fue aceptado.
      {cierre}</p>
    </div>""")
    if not bloques:
        return ""
    return f"""
  <section id="grafico-controles" class="grafico">
    <h2>1. Pruebas generadas que superan cada criterio de filtrado</h2>
    <p class="como-leer"><strong>Lectura del gráfico:</strong> cada barra indica cuántas de las pruebas generadas
    superan el criterio correspondiente. En el grupo B, la línea punteada señala el valor reportado por
    {REFERENCIA} para el mismo criterio.</p>
    {''.join(bloques)}
  </section>"""


def _grafico_ab(repos):
    """GRÁFICO 2: grupo A frente a grupo B, criterio por criterio (hipótesis H1 y H2)."""
    conteos = {g: _conteo([r for r in repos if r["grupo"] == g]) for g in ("A", "B")}
    if not (conteos["A"]["escritas"] and conteos["B"]["escritas"]):
        return ""
    filas = []
    for clave, titulo, hip in HIPOTESIS_AB + [("aporta", "Criterio 3 · Incrementan la cobertura (aceptadas)", "")]:
        celdas = []
        for g in ("A", "B"):
            c = conteos[g]
            if clave == "significativa":
                aprob = [x for r in repos if r["grupo"] == g for x in r["pruebas"] if x["aporta"]]
                v = _pct(sum(1 for x in aprob if x.get("significativa")), len(aprob))
            else:
                v = _pct(c[clave], c["escritas"])
            ancho = max(v or 0, 0.6)
            celdas.append(f'<div class="ab-fila"><span class="etiqueta-grupo g{g}">{g}</span>'
                          f'<div class="ab-pista"><div class="ab-barra g{g}" style="width:{ancho:.1f}%"></div></div>'
                          f'<strong>{_n(v, 0)}%</strong></div>')
        nota = (f' <span class="hip">({hip})</span>' if hip else
                ' <span class="hip">(descriptivo: con cobertura inicial nula, casi toda prueba válida incrementa '
                'la cobertura)</span>')
        filas.append(f'<div class="ab-control"><p class="ab-titulo">{_e(titulo)}{nota}</p>{"".join(celdas)}</div>')
    return f"""
  <section id="grafico-ab" class="grafico">
    <h2>2. Comparación entre grupos: efecto de la ausencia de pruebas preexistentes</h2>
    <p class="como-leer"><strong>Lectura del gráfico:</strong> para cada criterio, la barra A corresponde al grupo sin
    pruebas preexistentes y la barra B al grupo con suite preexistente. Ambos grupos se procesaron con el mismo
    procedimiento, el mismo modelo y la misma herramienta; la única condición que varía es la existencia de pruebas
    previas. La fila de verificación significativa se calcula sobre las pruebas aceptadas.</p>
    <div class="ab">{''.join(filas)}</div>
  </section>"""


def _que_es_aprobada():
    return """
  <section id="aprobada" class="recuadro">
    <h2>Criterios de filtrado de las pruebas generadas</h2>
    <p>Una <strong>prueba unitaria</strong> es un programa breve que verifica de forma automática que una parte
    del código funciona como corresponde. Por ejemplo, invoca una función que suma con los valores 2 y 3 y
    comprueba que el resultado sea 5; si una modificación posterior altera esa función, la prueba lo detecta.</p>
    <p>El modelo de lenguaje propone pruebas candidatas, pero no garantiza que sean correctas ni útiles. Por eso
    cada una se somete, de forma automática y sucesiva, a los tres criterios de filtrado del enfoque de
    Alshahwan et al. (2024):</p>
    <ol class="controles">
      <li><strong>Compilación.</strong> La prueba debe poder ejecutarse sin errores de sintaxis, de importación ni
        de nombres no definidos.</li>
      <li><strong>Estabilidad.</strong> Se ejecuta 5 veces y debe pasar en todas. Una prueba que falla, o cuyo
        resultado varía entre ejecuciones, no es confiable.</li>
      <li><strong>Incremento de cobertura.</strong> Debe ejecutar líneas del código fuente que ninguna prueba
        anterior ejecutaba. En el grupo B, esto incluye todas las pruebas escritas por los desarrolladores.</li>
    </ol>
    <p class="destacado">Una prueba <strong>aceptada</strong> es la que cumple los tres criterios. Solo las pruebas
    aceptadas se incorporan al proyecto; las demás se descartan sin intervención humana.</p>
  </section>"""


def _grafico_cobertura(repos):
    """GRÁFICO 3: cobertura alcanzada en cada proyecto, separando la cobertura previa a la generación."""
    filas = []
    for r in repos:
        fin = r.get("cob_fin")
        if fin is None:
            continue
        sem = r.get("cob_sem")
        base = sem if sem is not None else 0.0
        ia = max(fin - base, 0.0)
        origen = "atribuible a la importación de los módulos" if r["grupo"] == "A" else "de la suite preexistente"
        tip = (f"{r['nombre']}: {_n(base)}% {origen} + {_n(ia)} puntos de las pruebas aceptadas "
               f"= {_n(fin)}% de cobertura")
        seg_base = (f'<div class="seg seg-base" style="width:{base:.1f}%"><span>{_n(base, 0)}%</span></div>'
                    if base >= 0.5 else "")
        seg_ia = f'<div class="seg seg-ia" style="width:{ia:.1f}%"><span>+{_n(ia, 0)}</span></div>' if ia >= 0.5 else ""
        filas.append(f"""
      <div class="cob-proy">
        <span class="cob-nombre"><span class="etiqueta-grupo g{r['grupo']}">{r['grupo']}</span> {_e(r['nombre'])}</span>
        <div class="cob-pista" role="img" aria-label="{_e(tip)}" title="{_e(tip)}">{seg_base}{seg_ia}</div>
        <span class="cob-total"><strong>{_n(fin)}%</strong></span>
      </div>""")
    if not filas:
        return ""
    hay_base = any(r.get("cob_sem") is not None for r in repos)
    nota_base = ("" if hay_base else
                 '<p class="nota">Esta ejecución no midió la cobertura previa a la generación: todo el porcentaje se '
                 'muestra como incremento y puede estar sobrestimado.</p>')
    return f"""
  <section id="grafico-cobertura" class="grafico">
    <h2>3. Cobertura de código alcanzada en cada proyecto</h2>
    <p class="como-leer"><strong>Lectura del gráfico:</strong> cada barra representa la totalidad del código fuente
    de un proyecto (100 %); la letra indica su grupo. La <strong>cobertura</strong> es el porcentaje de líneas que las
    pruebas ejecutan; las líneas no ejecutadas no están verificadas por ninguna prueba.</p>
    <div class="cob-leyenda">
      <span><i class="muestra seg-base"></i> Cobertura previa a la generación (A: atribuible a la importación de los
        módulos; B: suite preexistente)</span>
      <span><i class="muestra seg-ia"></i> Incremento aportado por las pruebas aceptadas</span>
      <span><i class="muestra vacia"></i> Código no cubierto</span>
    </div>
    <div class="cob-grafico">{''.join(filas)}
      <div class="cob-escala" aria-hidden="true"><span></span><div><span>0%</span><span>50%</span><span>100%</span></div><span></span></div>
    </div>
    <p class="lectura">El primer tramo no se atribuye al modelo. En el grupo B corresponde a la cobertura de las
    pruebas escritas por los desarrolladores. En el grupo A corresponde a las líneas que Python ejecuta al importar
    un módulo (definiciones de funciones y clases, constantes), que cuentan como cubiertas aunque no exista ninguna
    prueba. <strong>El aporte del modelo es únicamente el segundo tramo.</strong> La cobertura se mide sobre todo el
    código fuente del proyecto, aunque solo se hayan generado pruebas para algunos de sus módulos.</p>
    {nota_base}
  </section>"""


def _embudo(r):
    return _filas_controles(r["conteo"], r["pct"], r["conteo"]["escritas"], con_meta=(r["grupo"] == "B"))


def _cobertura(r):
    fin = r.get("cob_fin")
    if fin is None:
        return ""
    sem = r.get("cob_sem")
    if sem is None:
        return f'<p class="cob-texto">Cobertura final: <strong>{_n(fin)}%</strong>.</p>'
    origen = "atribuible a la importación de los módulos" if r["grupo"] == "A" else "con la suite preexistente"
    return (f'<p class="cob-texto">Cobertura: <strong>{_n(sem)}%</strong> {origen} → <strong>{_n(fin)}%</strong> '
            f'con las pruebas aceptadas. Incremento atribuible al modelo: <strong>{_n(fin - sem)} puntos '
            f'porcentuales</strong>.</p>')


def _criterios(r):
    if not r.get("criterios"):
        return ""
    items = "".join(
        f'<li class="{"si" if c["cumple"] else "no"}"><span class="tilde" aria-hidden="true">{"✓" if c["cumple"] else "✗"}</span>'
        f'<span>{_e(CRITERIOS.get(c["criterio"], c["criterio"]))}<small>{_e(c["detalle"])}</small></span></li>'
        for c in r["criterios"])
    return f"""
      <details class="criterios">
        <summary>Criterios de selección verificados</summary>
        <ul>{items}</ul>
      </details>"""


def _tarjeta_repo(r, n_corridas):
    c = r["conteo"]
    errores = [e for e in r["estados"] if e != "completo"]
    aviso = ""
    if errores:
        aviso = "".join(f'<p class="aviso-estado">{_e(ESTADOS.get(e, e))}</p>' for e in sorted(set(errores)))
    if c["escritas"] == 0:
        cuerpo = '<p class="vacio">No se registraron pruebas para clasificar en este proyecto.</p>'
    else:
        verificacion = ""
        if c["aporta"]:
            sin = c["aporta"] - r["aceptadas_significativas"]
            verificacion = (f'<p class="nota-asercion">De las {c["aporta"]} pruebas aceptadas, '
                            f'<strong>{r["aceptadas_significativas"]}</strong> contienen una verificación significativa '
                            f'(comparan el resultado con un valor esperado o comprueban una excepción)'
                            + (f' y <strong>{sin}</strong> se limitan a ejecutar código o a comprobaciones triviales: '
                               f'incrementan la cobertura, pero difícilmente detectarían un defecto.' if sin else '.')
                            + '</p>')
        nota_corridas = (f'<p class="nota">Cantidades: suma de las {n_corridas} corridas. '
                         f'Porcentajes: promedio de las corridas.</p>' if n_corridas > 1 else "")
        cuerpo = f"""
      <div class="embudo">{_embudo(r)}</div>
      {_cobertura(r)}{verificacion}
      <p class="resumen-repo">En total, el modelo generó <strong>{c['escritas']}</strong> pruebas para este proyecto,
        de las cuales <strong>{c['aporta']}</strong> fueron aceptadas.</p>{nota_corridas}"""
    return f"""
    <article class="repo">
      <h3><span class="etiqueta-grupo g{r['grupo']}">{r['grupo']}</span> {_e(r['nombre'])}</h3>
      {aviso}{cuerpo}{_criterios(r)}
    </article>"""


def _comparacion(repos):
    """Las dos comparaciones del diseño, en tablas: B frente a la referencia y A frente a B."""
    con_datos = [r for r in repos if r["conteo"]["escritas"]]
    if not con_datos:
        return ""
    b = [r for r in con_datos if r["grupo"] == "B"]
    a = [r for r in con_datos if r["grupo"] == "A"]
    partes = []

    def mediana(rs, k):
        vals = [r["pct"].get(k) for r in rs if r["pct"].get(k) is not None]
        return st.median(vals) if vals else None

    if b:
        cab = "".join(f'<th class="num">{_e(r["nombre"])}</th>' for r in b)
        filas = []
        for clave, titulo, _ in ETAPAS[1:]:
            celdas = "".join(f'<td class="num">{_n(r["pct"].get(clave), 0)}%</td>' for r in b)
            med = mediana(b, clave)
            dif = "" if med is None else ("+" if med >= REFERENCIA_META[clave] else "−") + _n(abs(med - REFERENCIA_META[clave]), 0)
            filas.append(f"""
        <tr><th scope="row">{_e(titulo)}</th>{celdas}
          <td class="num"><strong>{_n(med, 0)}%</strong></td><td class="num">{REFERENCIA_META[clave]:.0f}%</td>
          <td class="num">{dif} p.p.</td></tr>""")
        partes.append(f"""
    <h3>4.1 Grupo B frente al estudio de referencia (replicación)</h3>
    <p>{REFERENCIA} reportaron, sobre proyectos de Meta con pruebas preexistentes, que del total de pruebas generadas
    el 75 % compiló, el 57 % fue estable y el 25 % incrementó la cobertura. El grupo B reproduce esa condición, por lo
    que admite la comparación.</p>
    <div class="tabla-scroll"><table>
      <thead><tr><th>Criterio</th>{cab}<th class="num">Grupo B (mediana)</th><th class="num">Referencia</th>
        <th class="num">Diferencia</th></tr></thead>
      <tbody>{''.join(filas)}</tbody>
    </table></div>
    <p class="nota">Aun bajo la misma condición, difieren el lenguaje de programación (Python frente a Kotlin), el
    modelo de lenguaje y la implementación de la herramienta (Qodo Cover es la implementación de código abierto del
    enfoque). La diferencia indica el grado de reproducción del resultado, no una comparación de desempeño.</p>""")
    if a and b:
        filas = []
        for clave, titulo, hip in HIPOTESIS_AB:
            ma, mb = mediana(a, clave), mediana(b, clave)
            if ma is None or mb is None:
                continue
            va = ma < mb
            filas.append(f"""
        <tr><th scope="row">{_e(titulo)}</th><td class="num">{_n(ma, 0)}%</td><td class="num">{_n(mb, 0)}%</td>
          <td>Grupo A inferior al grupo B ({hip})</td>
          <td class="{'va' if va else 'no-va'}">{'Consistente con la hipótesis' if va else 'No consistente con la hipótesis'}</td></tr>""")
        partes.append(f"""
    <h3>4.2 Grupo A frente a grupo B (pregunta de investigación)</h3>
    <p>Ambos grupos se procesaron con el mismo procedimiento, el mismo modelo y la misma herramienta; la única
    condición que varía es la existencia de pruebas preexistentes. Esta comparación responde la pregunta del
    estudio.</p>
    <div class="tabla-scroll"><table>
      <thead><tr><th>Indicador</th><th class="num">Grupo A</th><th class="num">Grupo B</th>
        <th>Resultado esperado (hipótesis)</th><th>Resultado observado</th></tr></thead>
      <tbody>{''.join(filas)}</tbody>
    </table></div>""")
    elif a:
        partes.append("""
    <p class="nota">Esta ejecución incluye solo proyectos del grupo A. Determinar el efecto de la ausencia de
    pruebas requiere contrastarlos con proyectos del grupo B procesados con el mismo procedimiento.</p>""")
    if a:
        partes.append(f"""
    <p class="nota"><strong>Por qué el grupo A no se contrasta con el estudio de referencia:</strong> cuando un proyecto
    no tiene ninguna prueba, prácticamente cualquier prueba que compile y sea estable incrementa la cobertura, por lo
    que el criterio 3 deja de filtrar. {REFERENCIA} trabajaron sobre proyectos con pruebas preexistentes; comparar su
    25 % con el grupo A contrastaría dos condiciones distintas.</p>""")
    n = len(con_datos)
    return f"""
  <section id="comparacion">
    <h2>4. Comparaciones</h2>
    {''.join(partes)}
    <p class="nota">Los porcentajes se calculan sobre el total de pruebas generadas en cada proyecto, salvo la
    verificación significativa, que se calcula sobre las pruebas aceptadas. Para cada grupo se informa la mediana
    entre proyectos.
    {'Con este número de proyectos, los valores constituyen una observación preliminar y no permiten conclusiones: ' if n < 10 else ''}el
    contraste estadístico (pruebas de Wilcoxon y de Mann-Whitney) se realizará sobre el conjunto completo del
    experimento.</p>
  </section>"""


def _ejemplos(repos):
    todas = [p for r in repos for p in r["pruebas"]]
    if not todas:
        return ""
    sin_asercion = [p for p in todas if p["aporta"] and not p.get("significativa")]
    trampa = sin_asercion[0] if sin_asercion else None
    mostrados = {_limpio(trampa["codigo_prueba"])} if trampa else set()
    bloques = []
    for cat, (titulo, explicacion) in CATEGORIAS.items():
        candidatas = [p for p in todas if p["categoria"] == cat]
        if not candidatas:
            continue
        orden = sorted(candidatas, key=lambda p: (_limpio(p["codigo_prueba"]) in mostrados, -p["tiene_asercion"]))
        p = orden[0]
        mostrados.add(_limpio(p["codigo_prueba"]))
        imports = (p["imports"] or "").strip().strip('"').strip()
        codigo = (imports + "\n\n" if imports else "") + _limpio(p["codigo_prueba"])
        motivo = ""
        if cat in ("falla", "no_compila"):
            lineas = [l.strip() for l in (p["salida_error"] or "").splitlines() if "Error" in l or "assert" in l]
            if lineas:
                motivo = f'<p class="motivo">Error informado por Python: <code>{_e(lineas[-1][:160])}</code></p>'
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
        <figcaption><strong>Aceptada, sin verificación significativa</strong>
          <span class="cuantas">{len(sin_asercion)} en total</span>
          <span class="explica">Cumple los tres criterios, pero se limita a ejecutar el código sin contrastar el
          resultado con un valor esperado: si la función tuviera un defecto, la prueba igualmente pasaría. Por esta
          razón el estudio informa por separado las pruebas aceptadas con verificación significativa.</span></figcaption>
        <pre><code>{_e(_limpio(p['codigo_prueba']))}</code></pre>
      </figure>"""
    return f"""
  <section id="ejemplos">
    <h2>7. Ejemplos de pruebas generadas en esta ejecución</h2>
    <p>Se presenta una prueba de cada categoría, tal como la generó el modelo. El título y la descripción de cada
    ejemplo indican el motivo de su aceptación o descarte; no es necesario leer el código.</p>
    {''.join(bloques)}{extra}
  </section>"""


def _limpio(codigo):
    """Quita la sangría común (las pruebas pueden venir indentadas como métodos de una clase)."""
    return textwrap.dedent((codigo or "").strip("\n")).strip()


def _detalles_tecnicos(ej, cfg, repos, almacen):
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
        ("Iteraciones por módulo", h.get("max_iteraciones")),
        ("Nivel de razonamiento del modelo", h.get("razonamiento") or "valor por defecto del modelo"),
        ("Diagnóstico de pruebas fallidas", "a partir de la salida de pytest, sin consultar al modelo"
         if h.get("analisis_fallas", "ia") == "local" else "solicitado al modelo (comportamiento original)"),
        ("Ejecuciones por prueba (estabilidad)", h.get("ejecuciones_por_prueba")),
        ("Corridas por proyecto", cfg.get("experimento", {}).get("corridas_por_repo")),
        ("Tiempo total de procesamiento", f"{_n(dur / 60)} minutos"),
        ("Consumo del modelo", f"{tokens:,} tokens".replace(",", ".")),
        ("Costo estimado", f"US$ {_n(costo, 4)}"),
        ("Entorno", "sin contenedor (modo de depuración)" if ej["imagen_docker"] == "sin-docker"
                    else f"contenedor Docker {str(ej['imagen_docker'])[:19]}"),
        ("Máquina", f"{maquina.get('sistema', '')}, Python {maquina.get('python', '')}"),
        ("Versión del pipeline", ej["commit_pipeline"]),
        ("Huella de la configuración", ej["huella_config"]),
    ]
    tabla = "".join(f"<tr><th scope='row'>{_e(a)}</th><td>{_e(b)}</td></tr>" for a, b in filas)
    uso = consumo_por_tipo(almacen, ej["id"])
    tabla_uso = ""
    if uso:
        filas_uso = "".join(
            f"<tr><th scope='row'>{_e(TIPOS_LLAMADA.get(t, t))}</th><td class='num'>{d.get('llamadas', 0)}</td>"
            f"<td class='num'>{d.get('entrada', 0):,}</td><td class='num'>{d.get('salida', 0):,}</td>"
            f"<td class='num'>{d.get('razonamiento', 0):,}</td></tr>".replace(",", ".")
            for t, d in sorted(uso.items(), key=lambda x: -(x[1].get('entrada', 0) + x[1].get('salida', 0))))
        tabla_uso = f"""
      <p>Consumo del modelo según el tipo de solicitud (los tokens de razonamiento están incluidos en la salida):</p>
      <div class="tabla-scroll"><table class="tecnica"><thead><tr><th>Tipo de solicitud</th><th class="num">Llamadas</th>
        <th class="num">Entrada</th><th class="num">Salida</th><th class="num">Razonamiento</th></tr></thead>
        <tbody>{filas_uso}</tbody></table></div>"""
    return f"""
  <section id="tecnico">
    <details>
      <summary>Detalles técnicos de la ejecución</summary>
      <p>Información necesaria para repetir exactamente esta ejecución (reproducibilidad, RNF-01).</p>
      <div class="tabla-scroll"><table class="tecnica">{tabla}</table></div>{tabla_uso}
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
    nombre_exp = {"prueba": "Prueba de funcionamiento", "piloto": "Prueba piloto"}.get(ej["experimento"], ej["experimento"])
    tarjetas = ""
    for g in ("A", "B"):
        del_grupo = [r for r in repos if r["grupo"] == g]
        if del_grupo:
            corto, titulo, _ = GRUPOS[g]
            tarjetas += (f'<h3 class="titulo-grupo"><span class="etiqueta-grupo g{g}">{corto}</span> {_e(titulo)}</h3>'
                         + "".join(_tarjeta_repo(r, n_corridas) for r in del_grupo))
    n_a = sum(1 for r in repos if r["grupo"] == "A")
    n_b = sum(1 for r in repos if r["grupo"] == "B")
    partes_grupos = []
    if n_a:
        partes_grupos.append(f"{n_a} sin pruebas preexistentes (grupo A)")
    if n_b:
        partes_grupos.append(f"{n_b} con una suite de pruebas preexistente (grupo B)")
    aporte_vals = [r["cob_fin"] - r["cob_sem"] for r in repos
                   if r.get("cob_sem") is not None and r.get("cob_fin") is not None]
    linea_aporte = (f"<li>El incremento de cobertura atribuible a las pruebas aceptadas osciló entre "
                    f"<strong>{_n(min(aporte_vals), 1)} y {_n(max(aporte_vals), 1)} puntos porcentuales</strong> por "
                    f"proyecto, descontada la cobertura previa a la generación.</li>" if aporte_vals else "")
    lineas_grupo = ""
    for g in ("A", "B"):
        c = _conteo([r for r in repos if r["grupo"] == g])
        if c["escritas"]:
            lineas_grupo += (f"<li>{GRUPOS[g][0]} ({GRUPOS[g][1].lower()}): <strong>{c['escritas']}</strong> pruebas "
                             f"generadas, <strong>{c['aporta']}</strong> aceptadas "
                             f"({_n(_pct(c['aporta'], c['escritas']), 0)} %).</li>")
    modelo = ej["modelo"].split("/")[-1]

    pagina = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Informe de ejecución — {_e(nombre_exp)} (ejecución {ej['id']})</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <header>
    <p class="contexto">Tesis FP-UNA · Evaluación empírica de la generación automatizada de pruebas unitarias con
      modelos de lenguaje en función de la cobertura de pruebas preexistente</p>
    <h1>{_e(nombre_exp)}</h1>
    <p class="entrada">Se solicitó al modelo de lenguaje <strong>{_e(modelo)}</strong>, a través de la herramienta
      Qodo Cover, la generación de pruebas unitarias para {n_repos} proyecto{'s' if n_repos != 1 else ''} de código
      abierto: {' y '.join(partes_grupos)}. El informe presenta la proporción de pruebas generadas que supera cada
      criterio de filtrado y compara los resultados entre ambas condiciones y con el estudio de referencia.</p>
    <p class="datos-ejecucion">Ejecución {ej['id']} · {_e(ej['inicio'])}</p>
  </header>
  {_grafico_controles(repos)}
  {_que_es_aprobada()}
  {_grafico_ab(repos)}
  {_grafico_cobertura(repos)}
  {_comparacion(repos)}

  <section id="resumen" class="recuadro">
    <h2>Síntesis</h2>
    <ul>
      <li>El modelo generó <strong>{escritas}</strong> pruebas en total, de las cuales <strong>{aprobadas}</strong>
        cumplieron los tres criterios de filtrado y fueron aceptadas.</li>
      {lineas_grupo}
      {linea_aporte}
      <li>El proceso completo —selección de proyectos, preparación de entornos, generación, filtrado y medición—
        se ejecutó de forma automatizada.</li>
    </ul>
  </section>

  <section id="proyectos">
    <h2>5. Resultados por proyecto</h2>
    <p class="leyenda">Los mismos criterios del gráfico 1, para cada proyecto. En el grupo B, la línea punteada
      (<span class="meta-muestra" aria-hidden="true"></span>) señala el valor del estudio de referencia.</p>
    {tarjetas}
  </section>

  <section id="pasos">
    <h2>6. Procedimiento</h2>
    <ol class="pasos">
      <li><strong>Selección.</strong> Se verificó que cada proyecto cumpla los criterios de inclusión: tamaño,
        instalación automatizada, licencia, actividad y, según el grupo, ausencia de pruebas (A) o una suite de
        pruebas preexistente que pasa (B).</li>
      <li><strong>Preparación.</strong> Cada proyecto se instaló en un entorno aislado (contenedor) con sus
        dependencias.</li>
      <li><strong>Cobertura inicial.</strong> En el grupo A se midió la cobertura nula de partida y la atribuible a la
        importación de los módulos; en el grupo B, la cobertura de la suite preexistente. Ese valor es la línea base,
        que no se atribuye al modelo.</li>
      <li><strong>Generación.</strong> Qodo Cover, implementación de código abierto del enfoque de {REFERENCIA},
        solicitó al modelo pruebas para cada módulo seleccionado. En el grupo B, las pruebas se incorporan al
        archivo de pruebas existente del módulo.</li>
      <li><strong>Filtrado y medición.</strong> Cada prueba se sometió a los tres criterios y se midió la cobertura
        final. En el grupo B, una prueba solo se acepta si ejecuta líneas que ninguna prueba de los desarrolladores
        ejecutaba.</li>
    </ol>
  </section>
  {_ejemplos(repos)}

  <section id="glosario">
    <h2>8. Glosario</h2>
    <dl>
      <dt>Prueba unitaria</dt><dd>Programa breve que verifica automáticamente que una parte del código funciona como
        corresponde; por ejemplo, que una función de suma devuelva 5 al recibir 2 y 3.</dd>
      <dt>Prueba aceptada</dt><dd>Prueba generada que cumple los tres criterios de filtrado: compila, es estable en 5
        ejecuciones e incrementa la cobertura. Es la única que se incorpora al proyecto.</dd>
      <dt>Cobertura de código</dt><dd>Porcentaje de líneas del código fuente que se ejecutan al correr las pruebas.
        Las líneas no ejecutadas no están verificadas por ninguna prueba.</dd>
      <dt>Cobertura atribuible a la importación</dt><dd>Al importar un módulo, Python ejecuta sus definiciones de
        funciones, clases y constantes; esas líneas cuentan como cubiertas aunque no exista ninguna prueba. Por eso
        se miden por separado y no se atribuyen al modelo.</dd>
      <dt>Verificación significativa</dt><dd>Comprobación que contrasta el resultado con un valor esperado o verifica
        que se produzca una excepción. Una prueba que solo comprueba que «se obtuvo algún resultado» no la contiene.
        Es el indicador de la hipótesis H2.</dd>
      <dt>Grupo A y grupo B</dt><dd>Grupo A: proyectos sin pruebas preexistentes. Grupo B: proyectos con una suite de
        pruebas escrita por sus desarrolladores. Ambos se procesan con el mismo procedimiento; la comparación entre
        ellos permite aislar el efecto de la ausencia de pruebas.</dd>
      <dt>Estudio de referencia</dt><dd>{REFERENCIA}, publicado en FSE 2024: aplicó el enfoque de generación con
        filtrado (TestGen-LLM) sobre productos de Meta que ya contaban con pruebas. El grupo B replica esa condición.</dd>
      <dt>Qodo Cover</dt><dd>Implementación de código abierto del enfoque de {REFERENCIA}. Solicita las pruebas al
        modelo y aplica los criterios de filtrado.</dd>
      <dt>Modelo de lenguaje (LLM)</dt><dd>Sistema de inteligencia artificial entrenado con grandes volúmenes de texto
        y código, capaz de generar código a partir de una instrucción.</dd>
    </dl>
  </section>
  {_detalles_tecnicos(ej, cfg, repos, almacen)}
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
.cob-proy, .cob-escala { display: grid; grid-template-columns: 11rem 1fr 4.5rem; gap: .9rem; align-items: center; }
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

.etiqueta-grupo { display: inline-block; min-width: 1.6rem; padding: .05rem .45rem; border-radius: 4px;
  font: 700 .8rem/1.4 system-ui, sans-serif; color: #fff; text-align: center; vertical-align: middle; }
.etiqueta-grupo.gA { background: var(--serie-1); } .etiqueta-grupo.gB { background: var(--serie-2); }
.grupo-bloque { margin: 1.6rem 0 2rem; }
.grupo-bloque h3 { margin-bottom: .2rem; }
.titulo-grupo { margin: 2rem 0 .4rem; }
.ab { display: grid; gap: 1.3rem; }
.ab-titulo { margin: 0 0 .4rem; font-weight: 600; }
.ab-titulo .hip { font-weight: 400; color: var(--tenue); font-size: .88rem; }
.ab-fila { display: grid; grid-template-columns: 2rem 1fr 3.5rem; gap: .7rem; align-items: center; margin: .25rem 0; }
.ab-fila strong { text-align: right; font-variant-numeric: tabular-nums; }
.ab-pista { height: 1.3rem; background: var(--codigo); border-radius: 3px; }
.ab-barra { height: 100%; border-radius: 3px; }
.ab-barra.gA { background: var(--serie-1); } .ab-barra.gB { background: var(--serie-2); }
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
