"""Historial y Backtesting automático en SQLite (Partidos + Player Props verificados vía Boxscore ESPN)."""
import datetime as dt
import math
import re
import shutil
import sqlite3
from pathlib import Path

import fuentes as F
import motor as M

DB = Path(__file__).parent / "historial.db"
BACKUPS = Path(__file__).parent / "backups"
MODEL_VERSION = "nba_v2_drtg"


def _backup_diario(fecha):
    """Copia de seguridad automática diaria de historial.db (mantiene últimos 14 días)."""
    if not DB.exists():
        return
    BACKUPS.mkdir(exist_ok=True)
    dst = BACKUPS / f"historial_{fecha:%Y%m%d}.db"
    if not dst.exists():
        shutil.copy2(DB, dst)
        viejos = sorted(BACKUPS.glob("historial_*.db"))[:-14]
        for v in viejos:
            v.unlink(missing_ok=True)


def _conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT, game_id TEXT, partido TEXT, hora TEXT,
        mercado TEXT, seleccion TEXT, cuota REAL, prob_real REAL,
        linea_alt REAL, home_nombre TEXT, away_nombre TEXT,
        estado TEXT, resultado TEXT DEFAULT 'PENDIENTE', marcador TEXT DEFAULT '',
        model_version TEXT DEFAULT 'v1',
        UNIQUE(fecha, game_id, mercado)
    )""")
    # Migración segura si la tabla ya existía sin model_version
    cols = {r[1] for r in c.execute("PRAGMA table_info(picks)").fetchall()}
    if "model_version" not in cols:
        c.execute("ALTER TABLE picks ADD COLUMN model_version TEXT DEFAULT 'v1'")
    c.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT, game_id TEXT, partido TEXT, captured_at TEXT,
        ml_home REAL, ml_away REAL, total_line REAL, spread_home REAL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_picks_fecha ON picks(fecha)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_odds_game ON odds_snapshots(game_id)")
    return c


def _linea_de(texto):
    m = re.search(r"(\d+(?:\.\d+)?)", str(texto or ""))
    return float(m.group(1)) if m else None


def registrar_jornada(fecha, partidos):
    """Guarda automáticamente todas las selecciones elegibles del día (Partidos + Player Props) y snapshot de cuotas."""
    _backup_diario(fecha)
    ahora = dt.datetime.now(F.VET).isoformat(timespec="seconds")
    with _conn() as c:
        for a in partidos:
            g = a["g"]
            dk = g.get("dk") or {}
            if dk.get("total") is not None:
                c.execute("""INSERT INTO odds_snapshots
                    (fecha, game_id, partido, captured_at, ml_home, ml_away, total_line, spread_home)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (str(fecha), g["id"], g["partido"], ahora, dk.get("ml_home"), dk.get("ml_away"),
                     dk.get("total"), dk.get("spread_home")))
            for s in a["sel"]:
                if M.bloqueado(s["estado"]):
                    continue
                c.execute("""INSERT INTO picks
                    (fecha, game_id, partido, hora, mercado, seleccion, cuota, prob_real,
                     linea_alt, home_nombre, away_nombre, estado, model_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(fecha, game_id, mercado) DO UPDATE SET
                      seleccion=excluded.seleccion, cuota=excluded.cuota,
                      prob_real=excluded.prob_real, linea_alt=excluded.linea_alt,
                      estado=excluded.estado, model_version=excluded.model_version
                    WHERE resultado='PENDIENTE'""",
                    (str(fecha), g["id"], g["partido"], g["hora"], s["tipo"], s["seleccion"],
                     s["cuota"], s["prob"], s.get("linea_alt"), g["home"]["nombre"],
                     g["away"]["nombre"], s["estado"], MODEL_VERSION))

            # Guardar también los Player Props sugeridos (Puntos, Rebotes, Asistencias, R+A, PRA)
            for p in a.get("props", []):
                if p.get("cuarentena"):
                    continue
                mercado = f"Prop {p['cat']} · {p['jugador']}"
                seleccion = f"{p['jugador']} — {p['prop']}"
                linea = _linea_de(p["prop"])
                c.execute("""INSERT INTO picks
                    (fecha, game_id, partido, hora, mercado, seleccion, cuota, prob_real,
                     linea_alt, home_nombre, away_nombre, estado, model_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(fecha, game_id, mercado) DO UPDATE SET
                      seleccion=excluded.seleccion, cuota=excluded.cuota,
                      prob_real=excluded.prob_real, linea_alt=excluded.linea_alt,
                      model_version=excluded.model_version
                    WHERE resultado='PENDIENTE'""",
                    (str(fecha), g["id"], g["partido"], g["hora"], mercado, seleccion,
                     p["cuota"], p["prob"], linea, g["home"]["nombre"],
                     g["away"]["nombre"], "LUZ VERDE", MODEL_VERSION))


def _boxscore_stats(game_id):
    """Descarga el boxscore de ESPN y devuelve {norm(nombre): {PTS, REB, AST}}."""
    try:
        sm = F.http(f"{F.ESPN}/summary?event={game_id}", ttl=86400 * 30)
    except Exception:
        return {}
    stats = {}
    for team_blk in sm.get("boxscore", {}).get("players", []):
        for cat in team_blk.get("statistics", []):
            hdrs = [k.upper() for k in (cat.get("labels") or cat.get("names") or cat.get("keys") or [])]
            if not hdrs:
                continue
            def _idx(*cands):
                for c_name in cands:
                    if c_name in hdrs:
                        return hdrs.index(c_name)
                return None
            i_pts = _idx("PTS", "POINTS")
            i_reb = _idx("REB", "REBOUNDS")
            i_ast = _idx("AST", "ASSISTS")
            for ath in cat.get("athletes", []):
                nom = ath.get("athlete", {}).get("displayName", "")
                vals = ath.get("stats", [])
                if not nom or not vals:
                    continue
                def _v(idx):
                    if idx is None or idx >= len(vals):
                        return 0
                    try:
                        return int(float(vals[idx]))
                    except ValueError:
                        return 0
                stats[F.norm(nom)] = dict(PTS=_v(i_pts), REB=_v(i_reb), AST=_v(i_ast))
    return stats


def verificar_resultados():
    """Consulta ESPN (marcadores + boxscore de jugadores) y liquida GANADA/PERDIDA/PUSH."""
    liquidados = 0
    box_cache = {}
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
                gid = r["game_id"]
                if gid not in finales:
                    continue
                sa, sh, tot = finales[gid]
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
                elif r["mercado"].startswith("Prop ") and r["linea_alt"] is not None:
                    if gid not in box_cache:
                        box_cache[gid] = _boxscore_stats(gid)
                    # mercado = "Prop ASISTENCIAS · LeBron James"
                    partes = r["mercado"].split(" · ", 1)
                    cat = partes[0].replace("Prop ", "").strip()
                    jug = F.norm(partes[1]) if len(partes) > 1 else ""
                    st = box_cache[gid].get(jug)
                    if st is not None:
                        val = (
                            st["PTS"] if cat == "PUNTOS" else
                            st["REB"] if cat == "REBOTES" else
                            st["AST"] if cat == "ASISTENCIAS" else
                            st["REB"] + st["AST"] if cat == "R+A" else
                            st["PTS"] + st["REB"] + st["AST"]
                        )
                        marc = f"{val} {cat}"
                        res = "GANADA" if val > r["linea_alt"] else "PERDIDA"
                if res != "PENDIENTE":
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

    por_fecha = {}
    por_mercado = {}
    brier_sum, logloss_sum, brier_n = 0.0, 0.0, 0
    for r in rows:
        d = por_fecha.setdefault(r["fecha"], dict(ganadas=0, perdidas=0, pendientes=0, beneficio_u=0.0))
        m_key = r["mercado"].split(" · ")[0] if r["mercado"].startswith("Prop ") else r["mercado"]
        m = por_mercado.setdefault(m_key, dict(mercado=m_key, ganadas=0, perdidas=0, pendientes=0, prob_sum=0.0, dec=0, beneficio_u=0.0))
        p_cl = max(0.01, min(0.99, float(r["prob_real"] or 0.5)))
        if r["resultado"] == "GANADA":
            d["ganadas"] += 1
            d["beneficio_u"] = round(d["beneficio_u"] + (r["cuota"] - 1), 2)
            m["ganadas"] += 1; m["dec"] += 1; m["prob_sum"] += p_cl
            m["beneficio_u"] = round(m["beneficio_u"] + (r["cuota"] - 1), 2)
            brier_sum += (p_cl - 1.0) ** 2; logloss_sum += -math.log(p_cl); brier_n += 1
        elif r["resultado"] == "PERDIDA":
            d["perdidas"] += 1
            d["beneficio_u"] = round(d["beneficio_u"] - 1, 2)
            m["perdidas"] += 1; m["dec"] += 1; m["prob_sum"] += p_cl
            m["beneficio_u"] = round(m["beneficio_u"] - 1, 2)
            brier_sum += (p_cl - 0.0) ** 2; logloss_sum += -math.log(1.0 - p_cl); brier_n += 1
        elif r["resultado"] == "PENDIENTE":
            d["pendientes"] += 1
            m["pendientes"] += 1

    # Drawdown máximo histórico (en unidades, orden cronológico)
    eq, peak, max_dd = 0.0, 0.0, 0.0
    for r in reversed(rows):
        if r["resultado"] == "GANADA":
            eq += (r["cuota"] - 1)
        elif r["resultado"] == "PERDIDA":
            eq -= 1.0
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    diag_mercados = []
    for m in por_mercado.values():
        dec = m["dec"]
        ac = round(m["ganadas"] / dec, 4) if dec else None
        pe = round(m["prob_sum"] / dec, 4) if dec else None
        muestra = "Confiable (100+)" if dec >= 100 else ("Tendencia (30-99)" if dec >= 30 else f"Muestra baja ({dec})")
        diag_mercados.append(dict(
            mercado=m["mercado"], ganadas=m["ganadas"], perdidas=m["perdidas"], pendientes=m["pendientes"],
            dec=dec, muestra=muestra,
            acierto=ac, prob_esperada=pe, sesgo=round(ac - pe, 4) if (ac is not None and pe is not None) else None,
            roi=round(m["beneficio_u"] / dec, 4) if dec else None, beneficio_u=m["beneficio_u"],
        ))
    diag_mercados.sort(key=lambda x: -(x["ganadas"] + x["perdidas"] + x["pendientes"]))

    return dict(
        total=len(rows), ganadas=gan, perdidas=per, pendientes=pen,
        acierto=round(gan / decididas, 4) if decididas else None,
        roi=round(beneficio / decididas, 4) if decididas else None,
        beneficio_u=round(beneficio, 2),
        max_dd_u=round(max_dd, 2),
        brier=round(brier_sum / brier_n, 4) if brier_n else None,
        log_loss=round(logloss_sum / brier_n, 4) if brier_n else None,
        por_fecha=por_fecha,
        por_mercado=diag_mercados,
        picks=rows[:500],
    )
