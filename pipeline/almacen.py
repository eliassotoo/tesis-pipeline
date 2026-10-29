"""
Almacén de resultados (SQLite). RF-11 (conjunto de datos estructurado) y RNF-03 (trazabilidad):
cada prueba queda asociada a su repositorio, corrida, modelo, parámetros y fecha.
"""

import json
import sqlite3
import time
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ejecuciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experimento TEXT, inicio TEXT, fin TEXT,
    modelo TEXT, api_base TEXT, huella_config TEXT, config_json TEXT,
    commit_pipeline TEXT, imagen_docker TEXT, maquina TEXT
);
CREATE TABLE IF NOT EXISTS seleccion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experimento TEXT, repo TEXT, origen TEXT, commit_repo TEXT,
    criterio TEXT, cumple INTEGER, detalle TEXT, fecha TEXT
);
CREATE TABLE IF NOT EXISTS corridas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id INTEGER, experimento TEXT, repo TEXT, corrida INTEGER, estado TEXT,
    cobertura_inicial REAL, cobertura_final REAL, duracion_seg REAL,
    cobertura_semillas REAL,
    tokens_entrada INTEGER, tokens_salida INTEGER, costo_usd REAL,
    modulos INTEGER, error TEXT, carpeta TEXT
);
CREATE TABLE IF NOT EXISTS pruebas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    corrida_id INTEGER, experimento TEXT, repo TEXT, corrida INTEGER, modulo TEXT,
    id_herramienta INTEGER, estado_herramienta TEXT, motivo TEXT, codigo_salida INTEGER,
    codigo_prueba TEXT, imports TEXT, salida_error TEXT,
    categoria TEXT, compila INTEGER, pasa INTEGER, aporta INTEGER, tiene_asercion INTEGER,
    modelo TEXT, fecha TEXT
);
"""


class Almacen:
    def __init__(self, ruta):
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(ruta)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(ESQUEMA)
        # Migración: bases creadas antes de agregar la cobertura con semillas
        columnas = {f["name"] for f in self.con.execute("PRAGMA table_info(corridas)").fetchall()}
        if "cobertura_semillas" not in columnas:
            self.con.execute("ALTER TABLE corridas ADD COLUMN cobertura_semillas REAL")
            self.con.commit()

    def nueva_ejecucion(self, cfg, commit_pipeline, imagen, maquina):
        cur = self.con.execute(
            "INSERT INTO ejecuciones (experimento, inicio, modelo, api_base, huella_config, config_json,"
            " commit_pipeline, imagen_docker, maquina) VALUES (?,?,?,?,?,?,?,?,?)",
            (cfg["experimento"]["nombre"], _ahora(), cfg["llm"]["modelo"], cfg["llm"]["api_base"],
             cfg["_huella"], json.dumps(cfg, default=str), commit_pipeline, imagen, json.dumps(maquina)))
        self.con.commit()
        return cur.lastrowid

    def cerrar_ejecucion(self, ejecucion_id):
        self.con.execute("UPDATE ejecuciones SET fin=? WHERE id=?", (_ahora(), ejecucion_id))
        self.con.commit()

    def registrar_criterio(self, experimento, repo, origen, commit, criterio, cumple, detalle):
        self.con.execute(
            "INSERT INTO seleccion (experimento, repo, origen, commit_repo, criterio, cumple, detalle, fecha)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (experimento, repo, origen, commit, criterio, int(bool(cumple)), detalle, _ahora()))
        self.con.commit()

    def registrar_corrida(self, ejecucion_id, experimento, resultado, carpeta, modelo, clasificar):
        mods = resultado.get("modulos", [])
        cur = self.con.execute(
            "INSERT INTO corridas (ejecucion_id, experimento, repo, corrida, estado, cobertura_inicial,"
            " cobertura_final, duracion_seg, tokens_entrada, tokens_salida, costo_usd, modulos, error, carpeta,"
            " cobertura_semillas) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ejecucion_id, experimento, resultado.get("repo"), resultado.get("corrida"), resultado.get("estado"),
             resultado.get("cobertura_inicial"), resultado.get("cobertura_final"), resultado.get("duracion_seg"),
             sum(m.get("tokens_entrada", 0) for m in mods), sum(m.get("tokens_salida", 0) for m in mods),
             round(sum(m.get("costo_usd", 0) for m in mods), 6), len(mods),
             (resultado.get("error") or "")[-3000:], str(carpeta), resultado.get("cobertura_semillas")))
        corrida_id = cur.lastrowid
        for m in mods:
            for intento in m.get("intentos", []):
                c = clasificar(intento)
                self.con.execute(
                    "INSERT INTO pruebas (corrida_id, experimento, repo, corrida, modulo, id_herramienta,"
                    " estado_herramienta, motivo, codigo_salida, codigo_prueba, imports, salida_error,"
                    " categoria, compila, pasa, aporta, tiene_asercion, modelo, fecha)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (corrida_id, experimento, resultado.get("repo"), resultado.get("corrida"), m["modulo"],
                     intento.get("id_herramienta"), intento.get("estado"), intento.get("motivo"),
                     intento.get("codigo_salida"), intento.get("codigo_prueba"), intento.get("imports"),
                     ((intento.get("stdout") or "") + "\n" + (intento.get("stderr") or ""))[-4000:],
                     c["categoria"], int(c["compila"]), int(c["pasa"]), int(c["aporta"]),
                     int(c["tiene_asercion"]), modelo, intento.get("momento")))
        self.con.commit()
        return corrida_id

    def consultar(self, sql, params=()):
        return [dict(f) for f in self.con.execute(sql, params).fetchall()]


def _ahora():
    return time.strftime("%Y-%m-%d %H:%M:%S")
