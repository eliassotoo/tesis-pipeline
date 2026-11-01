"""
Reportes de una ejecución en la terminal.

  resumen()  -> versión en lenguaje simple (la que se muestra por defecto al terminar).
  generar()  -> versión TÉCNICA: métricas por repositorio y por grupo, consumo del modelo por tipo de
                llamada y las dos comparaciones del diseño (sección 2.3 del anteproyecto):
                  · grupo B (con suite preexistente) frente a Alshahwan et al. (2024)  -> replicación
                  · grupo A (sin pruebas) frente al grupo B                            -> hipótesis H1 y H2
                El grupo A NO se compara con Meta: partiendo de cobertura nula casi cualquier prueba
                válida aporta, así que ese contraste no es válido. Se ve con: python -m pipeline reporte --tecnico

La comparación estadística formal (Wilcoxon, Mann-Whitney, bootstrap, tamaño del efecto) corresponde al
módulo M6 del hito H7; acá solo se describe. El informe para leer (HTML) está en informe.py.
"""

import csv
import json
import statistics as st
from pathlib import Path

REFERENCIA_META = {"compila": 75.0, "pasa": 57.0, "aporta": 25.0}
METRICAS = ("compila", "pasa", "aporta", "significativa")
NOMBRE_GRUPO = {"A": "Grupo A · sin pruebas preexistentes", "B": "Grupo B · con suite de pruebas preexistente"}
NOMBRE_METRICA = {"compila": "Compilan (criterio 1)", "pasa": "Estables (criterio 2)",
                  "aporta": "Incrementan la cobertura (criterio 3)",
                  "significativa": "Aceptadas con verificación significativa (H2)"}
# Hipótesis del anteproyecto (sección 2.1): el grupo A frente al grupo B
HIPOTESIS_AB = {"compila": ("menor", "H1"), "pasa": ("menor", "H1"), "significativa": ("menor", "H2")}
TIPOS_LLAMADA = {
    "generate_tests": "Generación de pruebas",
    "analyze_test_failure": "Diagnóstico de pruebas fallidas",
    "analyze_suite_test_headers_indentation": "Análisis del archivo de pruebas (sangría)",
    "analyze_test_insert_line": "Análisis del archivo de pruebas (inserción)",
}


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def metricas_pruebas(p):
    """Tasas sobre TODAS las pruebas escritas (denominador de Meta). «significativa»: entre las aprobadas."""
    n = len(p)
    aprobadas = [x for x in p if x["aporta"]]
    return {
        "candidatas": n,
        "compila": _pct(sum(x["compila"] for x in p), n),
        "pasa": _pct(sum(x["pasa"] for x in p), n),
        "aporta": _pct(len(aprobadas), n),
        "significativa": _pct(sum(1 for x in aprobadas if x.get("significativa")), len(aprobadas)),
        "aceptadas_con_asercion": _pct(sum(x["tiene_asercion"] for x in aprobadas), len(aprobadas)),
        "categorias": {c: sum(1 for x in p if x["categoria"] == c)
                       for c in ("aceptada", "sin_aporte", "falla", "no_compila", "error")},
        "aceptadas": len(aprobadas),
    }


def metricas_corrida(almacen, corrida_id):
    return metricas_pruebas(almacen.consultar(
        "SELECT compila, pasa, aporta, tiene_asercion, significativa, categoria FROM pruebas WHERE corrida_id=?",
        (corrida_id,)))


def datos_por_repo(almacen, ejecucion_id):
    """
    Una entrada por repositorio con su grupo y sus métricas. Unidad de análisis: el repositorio
    (porcentaje = promedio de sus corridas, sección 3.4.6). Solo cuentan las corridas sin error de la IA.
    """
    corridas = almacen.consultar("SELECT * FROM corridas WHERE ejecucion_id=? ORDER BY grupo, repo, corrida",
                                 (ejecucion_id,))
    repos = {}
    for c in corridas:
        r = repos.setdefault(c["repo"], {"nombre": c["repo"], "grupo": c.get("grupo") or "A", "corridas": [],
                                         "por_corrida": []})
        r["corridas"].append(c)
        m = metricas_corrida(almacen, c["id"])
        if m["candidatas"]:
            r["por_corrida"].append(m)
    for r in repos.values():
        r["pct"] = {}
        for k in METRICAS:
            vals = [m[k] for m in r["por_corrida"] if m[k] is not None]
            r["pct"][k] = st.mean(vals) if vals else None
        completas = [c for c in r["corridas"] if c["estado"] == "completo"]
        r["cob_ini"] = _media([c["cobertura_inicial"] for c in completas])
        r["cob_fin"] = _media([c["cobertura_final"] for c in completas])
        r["cob_base"] = _media([c.get("cobertura_base") if c.get("cobertura_base") is not None
                                else c.get("cobertura_semillas") for c in completas])
    return list(repos.values())


def resumen_grupos(repos):
    """{grupo: {metrica: mediana entre repositorios, 'n': repos con datos}}"""
    salida = {}
    for g in ("A", "B"):
        rs = [r for r in repos if r["grupo"] == g and r["por_corrida"]]
        if not rs:
            continue
        d = {"n": len(rs)}
        for k in METRICAS:
            vals = [r["pct"][k] for r in rs if r["pct"][k] is not None]
            d[k] = st.median(vals) if vals else None
        aportes = [r["cob_fin"] - r["cob_base"] for r in rs if r["cob_fin"] is not None and r["cob_base"] is not None]
        d["aporte_cobertura_pp"] = st.median(aportes) if aportes else None
        salida[g] = d
    return salida


def consumo(almacen, ejecucion_id):
    """Consumo del modelo por tipo de llamada (si la imagen tiene los ajustes del estudio)."""
    total = {}
    for c in almacen.consultar("SELECT consumo_json FROM corridas WHERE ejecucion_id=?", (ejecucion_id,)):
        try:
            datos = json.loads(c["consumo_json"] or "{}")
        except ValueError:
            continue
        for tipo, t in datos.items():
            acc = total.setdefault(tipo, {})
            for k, v in t.items():
                acc[k] = acc.get(k, 0) + v
    return total


def _media(vals):
    vals = [v for v in vals if v is not None]
    return st.mean(vals) if vals else None


def _ultima(almacen, ejecucion_id):
    if ejecucion_id is None:
        fila = almacen.consultar("SELECT MAX(id) AS id FROM ejecuciones")
        ejecucion_id = fila[0]["id"] if fila else None
    return ejecucion_id


def _coma(v, dec=1):
    return "-" if v is None else f"{v:.{dec}f}".replace(".", ",")


def resumen(almacen, ejecucion_id=None, ruta_informe=None):
    """Resumen en lenguaje simple: el embudo de cada proyecto, por grupo, y dónde está el informe completo."""
    ejecucion_id = _ultima(almacen, ejecucion_id)
    if not ejecucion_id:
        print("Todavía no hay ejecuciones registradas.")
        return
    ej = almacen.consultar("SELECT * FROM ejecuciones WHERE id=?", (ejecucion_id,))[0]
    repos = datos_por_repo(almacen, ejecucion_id)
    linea = "-" * 72
    print(f"\n{linea}\nRESUMEN DE LA EJECUCIÓN {ej['id']}  (modelo: {ej['modelo']})")
    print(linea)
    for g in ("A", "B"):
        del_grupo = [r for r in repos if r["grupo"] == g]
        if not del_grupo:
            continue
        print(f"\n{NOMBRE_GRUPO[g].upper()}")
        for r in del_grupo:
            ids = tuple(c["id"] for c in r["corridas"])
            p = almacen.consultar(f"SELECT compila, pasa, aporta FROM pruebas WHERE corrida_id IN "
                                  f"({','.join('?' * len(ids))})", ids)
            n = len(p)
            print(f"\nProyecto {r['nombre']}" + (f" ({len(r['corridas'])} corridas)" if len(r["corridas"]) > 1 else ""))
            malos = [c["estado"] for c in r["corridas"] if c["estado"] != "completo"]
            if malos:
                print(f"   Atención: {len(malos)} corrida(s) sin terminar ({', '.join(sorted(set(malos)))})")
            if not n:
                print("   No hubo pruebas para clasificar.")
                continue
            etapas = [("pruebas generadas", n), ("compilan (criterio 1)", sum(x["compila"] for x in p)),
                      ("estables en 5 ejecuciones (criterio 2)", sum(x["pasa"] for x in p)),
                      ("ACEPTADAS: incrementan cobertura (3)", sum(x["aporta"] for x in p))]
            for i, (texto, cant) in enumerate(etapas):
                pct = "" if i == 0 else f"{_coma(100 * cant / n, 0):>5}%"
                print(f"   {cant:>4}  {texto:<40}{pct}")
            if r["cob_fin"] is not None and r["cob_base"] is not None:
                base = "atribuible a la importación" if g == "A" else "de la suite preexistente"
                print(f"   Cobertura: {_coma(r['cob_base'])}% {base} -> {_coma(r['cob_fin'])}% con las aceptadas "
                      f"(incremento atribuible al modelo: {_coma(r['cob_fin'] - r['cob_base'])} p.p.)")
    grupos = resumen_grupos(repos)
    if "B" in grupos:
        b = grupos["B"]
        print(f"\n{linea}\nGRUPO B FRENTE AL ESTUDIO DE REFERENCIA (Alshahwan et al., 2024; misma condición)")
        for k in ("compila", "pasa", "aporta"):
            print(f"   {NOMBRE_METRICA[k]:<46} B {_coma(b[k], 0):>4}%   Referencia {REFERENCIA_META[k]:.0f}%")
    if "A" in grupos and "B" in grupos:
        a, b = grupos["A"], grupos["B"]
        print(f"\nGRUPO A FRENTE A GRUPO B (efecto de la ausencia de pruebas preexistentes)")
        for k in ("compila", "pasa", "significativa"):
            print(f"   {NOMBRE_METRICA[k]:<46} A {_coma(a[k], 0):>4}%   B {_coma(b[k], 0):>4}%")
    elif "A" in grupos:
        print("\n(Esta ejecución no tiene proyectos del grupo B: no hay contra qué comparar el grupo A.)")
    total_usd = sum(c["costo_usd"] or 0 for c in almacen.consultar(
        "SELECT costo_usd FROM corridas WHERE ejecucion_id=?", (ejecucion_id,)))
    print(f"\nCosto estimado de la ejecución: US$ {_coma(total_usd, 3)}")
    print(f"\n{linea}")
    if ruta_informe:
        print(f"Informe completo para leer: {ruta_informe}")
        print("   Para abrirlo:  python -m pipeline ver-informe")
        print(f"   Datos crudos (CSV) en: {Path(ruta_informe).parent / 'csv'}")
    print("   Tabla técnica:  python -m pipeline reporte --tecnico")
    print(linea)


def generar(almacen, ejecucion_id=None, carpeta_csv=None):
    ejecucion_id = _ultima(almacen, ejecucion_id)
    if not ejecucion_id:
        print("Todavía no hay ejecuciones registradas.")
        return
    ej = almacen.consultar("SELECT * FROM ejecuciones WHERE id=?", (ejecucion_id,))[0]
    corridas = almacen.consultar("SELECT * FROM corridas WHERE ejecucion_id=? ORDER BY grupo, repo, corrida",
                                 (ejecucion_id,))
    linea = "=" * 108
    print(f"\n{linea}\nREPORTE · ejecución #{ej['id']} · experimento '{ej['experimento']}' · modelo {ej['modelo']}")
    print(f"Inicio {ej['inicio']} · fin {ej['fin']} · pipeline {ej['commit_pipeline']} · config {ej['huella_config']}")
    print(linea)
    print(f"{'G':<2}{'Repositorio':<26}{'Corr':>5}{'Estado':>16}{'Cob.ini':>9}{'Base':>7}{'Cob.fin':>9}{'Cand.':>7}"
          f"{'%Comp':>7}{'%Pasa':>7}{'%Aporta':>9}{'%Signif':>9}{'Tokens':>10}{'USD':>9}")
    total = {}
    for c in corridas:
        m = metricas_corrida(almacen, c["id"])
        for k, v in m["categorias"].items():
            total[k] = total.get(k, 0) + v
        tok = (c["tokens_entrada"] or 0) + (c["tokens_salida"] or 0)
        print(f"{(c.get('grupo') or 'A'):<2}{c['repo'][:25]:<26}{c['corrida']:>5}{c['estado'][:15]:>16}"
              f"{_f(c['cobertura_inicial']):>9}{_f(c.get('cobertura_base')):>7}{_f(c['cobertura_final']):>9}"
              f"{m['candidatas']:>7}{_f(m['compila']):>7}{_f(m['pasa']):>7}{_f(m['aporta']):>9}"
              f"{_f(m['significativa']):>9}{tok:>10}{_f(c['costo_usd'], 4):>9}")
        if c["estado"] != "completo" and c["error"]:
            print(f"{'':<7}motivo: {c['error'].strip().splitlines()[-1][:90]}")
    print(linea)
    print("Categorías (todas las corridas): " + ", ".join(f"{k} {v}" for k, v in total.items()))

    repos = datos_por_repo(almacen, ejecucion_id)
    grupos = resumen_grupos(repos)
    print(linea)
    print("Unidad de análisis: repositorio (promedio de sus corridas). Valores: mediana entre repositorios.")
    if "B" in grupos:
        b = grupos["B"]
        print(f"\nREPLICACIÓN · grupo B (n={b['n']}) frente a Alshahwan et al. (2024)")
        for k, ref in REFERENCIA_META.items():
            if b[k] is not None:
                print(f"   {k:<8} B {b[k]:6.1f}%   Meta {ref:.0f}%   diferencia {b[k] - ref:+.1f} pp")
    else:
        print("\nREPLICACIÓN · sin proyectos del grupo B en esta ejecución")
    if "A" in grupos and "B" in grupos:
        a, b = grupos["A"], grupos["B"]
        print(f"\nEFECTO DE NO TENER PRUEBAS · grupo A (n={a['n']}) frente a grupo B (n={b['n']})")
        for k, (sentido, hip) in HIPOTESIS_AB.items():
            if a[k] is None or b[k] is None:
                continue
            va = a[k] < b[k] if sentido == "menor" else a[k] > b[k]
            print(f"   {k:<14} A {a[k]:6.1f}%   B {b[k]:6.1f}%   {hip}: se esperaba A {sentido} que B "
                  f"-> {'coincide' if va else 'no coincide'}")
        print(f"   aporta         A {_f(a['aporte_cobertura_pp'])} pp   B {_f(b['aporte_cobertura_pp'])} pp de cobertura "
              "(solo descriptivo: en A partir de cero infla el aporte)")
    print("   (El contraste estadístico formal —Wilcoxon y Mann-Whitney— corresponde al hito H7.)")

    uso = consumo(almacen, ejecucion_id)
    print(linea)
    if uso:
        print("CONSUMO DEL MODELO POR TIPO DE LLAMADA")
        print(f"   {'Tipo':<44}{'Llamadas':>9}{'Entrada':>11}{'Salida':>10}{'Razonam.':>10}{'Caché':>9}")
        for tipo, t in sorted(uso.items(), key=lambda x: -(x[1].get("entrada", 0) + x[1].get("salida", 0))):
            print(f"   {TIPOS_LLAMADA.get(tipo, tipo)[:43]:<44}{t.get('llamadas', 0):>9}{t.get('entrada', 0):>11}"
                  f"{t.get('salida', 0):>10}{t.get('razonamiento', 0):>10}{t.get('cache', 0):>9}")
        print("   (Los tokens de razonamiento ya están incluidos en «Salida» y se cobran como salida.)")
    else:
        print("Consumo por tipo de llamada: no disponible (ejecución hecha con una imagen anterior).")
    print(linea)

    if carpeta_csv:
        exportar_csv(almacen, ejecucion_id, Path(carpeta_csv))


def exportar_csv(almacen, ejecucion_id, carpeta, silencioso=False):
    """RF-11: conjunto de datos estructurado y exportable."""
    carpeta.mkdir(parents=True, exist_ok=True)
    consultas = {
        "corridas.csv": ("SELECT * FROM corridas WHERE ejecucion_id=?", (ejecucion_id,)),
        "pruebas.csv": ("SELECT p.* FROM pruebas p JOIN corridas c ON p.corrida_id=c.id WHERE c.ejecucion_id=?",
                        (ejecucion_id,)),
        "seleccion.csv": ("SELECT * FROM seleccion WHERE experimento=(SELECT experimento FROM ejecuciones WHERE id=?)",
                          (ejecucion_id,)),
    }
    for nombre, (sql, params) in consultas.items():
        filas = almacen.consultar(sql, params)
        with open(carpeta / nombre, "w", newline="", encoding="utf-8") as f:
            if filas:
                w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
                w.writeheader()
                w.writerows(filas)
    if not silencioso:
        print(f"CSV exportados en {carpeta}/ (corridas.csv, pruebas.csv, seleccion.csv)")


def _f(v, dec=1):
    return "-" if v is None else f"{v:.{dec}f}"
