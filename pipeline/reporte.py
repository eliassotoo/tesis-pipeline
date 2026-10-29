"""
Reportes de una ejecución en la terminal.

  resumen()  -> versión en lenguaje simple (la que se muestra por defecto al terminar).
  generar()  -> versión TÉCNICA: métricas por repositorio, categorías y comparación PRELIMINAR con
                Alshahwan et al. (2024). Se ve con: python -m pipeline reporte --tecnico

La comparación estadística formal (Wilcoxon, bootstrap, tamaño del efecto) corresponde al módulo M6
del hito H7; acá solo se describe. El informe para leer (HTML) está en informe.py.
"""

import csv
import statistics as st
from pathlib import Path

REFERENCIA_META = {"compila": 75.0, "pasa": 57.0, "aporta": 25.0}


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def metricas_corrida(almacen, corrida_id):
    p = almacen.consultar("SELECT compila, pasa, aporta, tiene_asercion, categoria FROM pruebas WHERE corrida_id=?",
                          (corrida_id,))
    n = len(p)
    aceptadas = [x for x in p if x["aporta"]]
    return {
        "candidatas": n,
        "compila": _pct(sum(x["compila"] for x in p), n),
        "pasa": _pct(sum(x["pasa"] for x in p), n),
        "aporta": _pct(len(aceptadas), n),
        "aceptadas_con_asercion": _pct(sum(x["tiene_asercion"] for x in aceptadas), len(aceptadas)),
        "categorias": {c: sum(1 for x in p if x["categoria"] == c)
                       for c in ("aceptada", "sin_aporte", "falla", "no_compila", "error")},
        "aceptadas": len(aceptadas),
    }


def _ultima(almacen, ejecucion_id):
    if ejecucion_id is None:
        fila = almacen.consultar("SELECT MAX(id) AS id FROM ejecuciones")
        ejecucion_id = fila[0]["id"] if fila else None
    return ejecucion_id


def _coma(v, dec=1):
    return "-" if v is None else f"{v:.{dec}f}".replace(".", ",")


def resumen(almacen, ejecucion_id=None, ruta_informe=None):
    """Resumen en lenguaje simple: el embudo de cada proyecto y dónde está el informe completo."""
    ejecucion_id = _ultima(almacen, ejecucion_id)
    if not ejecucion_id:
        print("Todavía no hay ejecuciones registradas.")
        return
    ej = almacen.consultar("SELECT * FROM ejecuciones WHERE id=?", (ejecucion_id,))[0]
    corridas = almacen.consultar("SELECT * FROM corridas WHERE ejecucion_id=? ORDER BY repo, corrida", (ejecucion_id,))
    linea = "-" * 72
    print(f"\n{linea}\nRESUMEN DE LA EJECUCIÓN {ej['id']}  (modelo: {ej['modelo']})")
    print(linea)
    repos = {}
    for c in corridas:
        repos.setdefault(c["repo"], []).append(c)
    for repo, lista in repos.items():
        ids = tuple(c["id"] for c in lista)
        p = almacen.consultar(f"SELECT compila, pasa, aporta FROM pruebas WHERE corrida_id IN "
                              f"({','.join('?' * len(ids))})", ids)
        n = len(p)
        print(f"\nProyecto {repo}" + (f" ({len(lista)} corridas)" if len(lista) > 1 else ""))
        malos = [c["estado"] for c in lista if c["estado"] != "completo"]
        if malos:
            print(f"   Atención: {len(malos)} corrida(s) sin terminar ({', '.join(sorted(set(malos)))})")
        if not n:
            print("   No hubo pruebas para clasificar.")
            continue
        etapas = [("escritas por la IA", n), ("arrancan (control 1)", sum(x["compila"] for x in p)),
                  ("dan bien las 5 veces (control 2)", sum(x["pasa"] for x in p)),
                  ("APROBADAS (control 3)", sum(x["aporta"] for x in p))]
        for i, (texto, cant) in enumerate(etapas):
            pct = "" if i == 0 else f"{_coma(100 * cant / n, 0):>5}%"
            print(f"   {cant:>4}  {texto:<34}{pct}")
        completas = [c for c in lista if c["estado"] == "completo"]
        if completas:
            ini = st.mean(c["cobertura_inicial"] for c in completas)
            fin = st.mean(c["cobertura_final"] or 0 for c in completas)
            sem = [c.get("cobertura_semillas") for c in completas if c.get("cobertura_semillas") is not None]
            if sem:
                base = st.mean(sem)
                print(f"   Código revisado por pruebas: {_coma(ini)}% sin pruebas -> {_coma(base)}% solo importando "
                      f"-> {_coma(fin)}% con las pruebas de la IA")
                print(f"   Aporte atribuible a la IA: {_coma(fin - base)} puntos")
            else:
                print(f"   Código revisado por pruebas: {_coma(ini)}% antes -> {_coma(fin)}% después")
    print(f"\n{linea}")
    if ruta_informe:
        print(f"Informe completo para leer: {ruta_informe}")
        print("   Para abrirlo:  python -m pipeline ver-informe")
        print(f"   Datos crudos (CSV) en: {Path(ruta_informe).parent / 'csv'}")
    print("   Tabla técnica:  python -m pipeline reporte --tecnico")
    print(linea)


def generar(almacen, ejecucion_id=None, carpeta_csv=None):
    if ejecucion_id is None:
        fila = almacen.consultar("SELECT MAX(id) AS id FROM ejecuciones")
        ejecucion_id = fila[0]["id"] if fila else None
    if not ejecucion_id:
        print("Todavía no hay ejecuciones registradas.")
        return
    ej = almacen.consultar("SELECT * FROM ejecuciones WHERE id=?", (ejecucion_id,))[0]
    corridas = almacen.consultar("SELECT * FROM corridas WHERE ejecucion_id=? ORDER BY repo, corrida", (ejecucion_id,))

    linea = "=" * 100
    print(f"\n{linea}\nREPORTE · ejecución #{ej['id']} · experimento '{ej['experimento']}' · modelo {ej['modelo']}")
    print(f"Inicio {ej['inicio']} · fin {ej['fin']} · pipeline {ej['commit_pipeline']} · config {ej['huella_config']}")
    print(linea)
    print(f"{'Repositorio':<28}{'Corr':>5}{'Estado':>16}{'Cob.ini':>9}{'Cob.fin':>9}{'Cand.':>7}"
          f"{'%Comp':>7}{'%Pasa':>7}{'%Aporta':>9}{'Tokens':>10}{'USD':>9}")
    por_repo = {}
    for c in corridas:
        m = metricas_corrida(almacen, c["id"])
        por_repo.setdefault(c["repo"], []).append(m)
        tok = (c["tokens_entrada"] or 0) + (c["tokens_salida"] or 0)
        print(f"{c['repo'][:27]:<28}{c['corrida']:>5}{c['estado'][:15]:>16}{_f(c['cobertura_inicial']):>9}"
              f"{_f(c['cobertura_final']):>9}{m['candidatas']:>7}{_f(m['compila']):>7}{_f(m['pasa']):>7}"
              f"{_f(m['aporta']):>9}{tok:>10}{_f(c['costo_usd'], 4):>9}")
        if c["estado"] != "completo" and c["error"]:
            print(f"{'':<5}motivo: {c['error'].strip().splitlines()[-1][:90]}")

    print(linea)
    print("Detalle de categorías (todas las corridas):")
    total = {}
    for lista in por_repo.values():
        for m in lista:
            for k, v in m["categorias"].items():
                total[k] = total.get(k, 0) + v
    for k, v in total.items():
        print(f"   {k:<12} {v}")

    # Unidad de análisis: el repositorio (promedio de sus corridas), como dice la sección 3.4.6
    print(linea)
    print("Comparación PRELIMINAR con Alshahwan et al. (2024) — unidad de análisis: repositorio")
    for metrica, ref in REFERENCIA_META.items():
        valores = [st.mean([m[metrica] for m in lista if m[metrica] is not None])
                   for lista in por_repo.values() if any(m[metrica] is not None for m in lista)]
        if not valores:
            print(f"   {metrica:<8} sin datos")
            continue
        med = st.median(valores)
        print(f"   {metrica:<8} mediana {med:6.1f}%  (n={len(valores)} repos)   referencia Meta {ref:.0f}%   "
              f"diferencia {med - ref:+.1f} pp")
    print("   (El contraste estadístico formal — Wilcoxon para una muestra — corresponde al hito H7.)")
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
