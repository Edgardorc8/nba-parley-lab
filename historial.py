"""Historial y Backtesting automático en SQLite (sin dependencias externas)."""
import datetime as dt
import sqlite3
from pathlib import Path

import fuentes as F
import motor as M

DB = Path(__file__).parent / "historial.db"


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT, game_id TEXT, partido TEXT, hora TEXT,
        mercado TEXT, seleccion TEXT, cuota REAL, prob_real REAL,
        linea_alt REAL, home_nombre TEXT, away_nombre TEXT,
        estado TEXT, resultado TEXT DEFAULT 'PENDIENTE', marcador TEXT DEFAULT '',
        UNIQUE(fecha, game_id, mercado)
    )""")
    return c


def registrar_jornada(fecha, partidos):
    """Guarda automáticamente todas las selecciones elegibles (no bloqueadas) del día."""
    with _conn() as c:
        for a in partidos:
            g = a["g"]
            for s in a["sel"]:
                if M.bloqueado(s["estado"]):
                    continue
                c.execute("""INSERT INTO picks
                    (fecha, game_id, partido, hora, mercado, seleccion, cuota, prob_real,
                     linea_alt, home_nombre, away_nombre, estado)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(fecha, game_id, mercado) DO UPDATE SET
                      seleccion=excluded.seleccion, cuota=excluded.cuota,
                      prob_real=excluded.prob_real, linea_alt=excluded.linea_alt,
                      estado=excluded.estado
                    WHERE resultado='PENDIENTE'""",
                    (str(fecha), g["id"], g["partido"], g["hora"], s["tipo"], s["seleccion"],
                     s["cuota"], s["prob"], s.get("linea_alt"), g["home"]["nombre"],
                     g["away"]["nombre"], s["estado"]))


def verificar_resultados():
    """Consulta ESPN para las fechas con picks pendientes y liquida GANADA/PERDIDA/PUSH."""
    liquidados = 0
    with _conn() as c:
        fechas = [r["fecha"] for r in c.execute("SELECT DISTINCT fecha FROM picks WHERE resultado='PENDIENTE'")]
        for f_str in fechas:
            f = dt.date.fromisoformat(f_str)
            try:
                sb = F.http(f"{F.ESPN}/scoreboard?dates={f:%Y%m%d}", ttl=300)
            except Exception:
                continue
            finales = {}
            for e in sb.get("events", []):
                comp = e["competitions"][0]
                if not comp["status"]["type"].get("completed"):
                    continue
                teams = {x["homeAway"]: x for x in comp["competitors"]}
                sh = int(float(teams["home"]["score"]))
                sa = int(float(teams["away"]["score"]))
                finales[e["id"]] = (sa, sh, sa + sh)
            if not finales:
                continue
            for r in c.execute("SELECT * FROM picks WHERE fecha=? AND resultado='PENDIENTE'", (f_str,)).fetchall():
                if r["game_id"] not in finales:
                    continue
                sa, sh, tot = finales[r["game_id"]]
                marc = f"{sa}-{sh} ({tot})"
                res = "PENDIENTE"
                if r["mercado"] == "Ganador (P+)":
                    eligio_home = r["home_nombre"] in r["seleccion"]
                    gano = (sh > sa) if eligio_home else (sa > sh)
                    res = "GANADA" if gano else "PERDIDA"
                elif r["mercado"] == "Total Alt. UNDER" and r["linea_alt"] is not None:
                    res = "GANADA" if tot < r["linea_alt"] else ("PUSH" if tot == r["linea_alt"] else "PERDIDA")
                elif r["mercado"] == "Total Alt. OVER" and r["linea_alt"] is not None:
                    res = "GANADA" if tot > r["linea_alt"] else ("PUSH" if tot == r["linea_alt"] else "PERDIDA")
                c.execute("UPDATE picks SET resultado=?, marcador=? WHERE id=?", (res, marc, r["id"]))
                liquidados += 1
    return liquidados


def resumen():
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM picks ORDER BY fecha DESC, hora ASC, prob_real DESC")]
    gan = sum(1 for r in rows if r["resultado"] == "GANADA")
    per = sum(1 for r in rows if r["resultado"] == "PERDIDA")
    pen = sum(1 for r in rows if r["resultado"] == "PENDIENTE")
    decididas = gan + per
    beneficio = sum((r["cuota"] - 1) for r in rows if r["resultado"] == "GANADA") - per
    return dict(
        total=len(rows), ganadas=gan, perdidas=per, pendientes=pen,
        acierto=round(gan / decididas, 4) if decididas else None,
        roi=round(beneficio / decididas, 4) if decididas else None,
        beneficio_u=round(beneficio, 2),
        picks=rows[:200],
    )
