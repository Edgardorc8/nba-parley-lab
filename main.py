"""NBA Parley Lab: descarga todo, analiza y genera el Excel del día.
Uso:  python main.py            (hoy, hora Venezuela)
      python main.py 2026-11-22
"""
import csv
import datetime as dt
import json
import sys
from pathlib import Path

import fuentes as F
import historial as H
import motor as M
from excel import generar_excel

BASE = Path(__file__).parent
ENTRADA = BASE / "entrada"
REPORTES = BASE / "reportes"
CAMPOS = ["partido", "ml_local", "ml_visita", "under_linea", "under_cuota", "over_linea", "over_cuota"]


def archivo_cuotas(fecha):
    ENTRADA.mkdir(exist_ok=True)
    return ENTRADA / f"triunfobet_{fecha:%Y%m%d}.csv"


def leer_cuotas(fecha, juegos=None):
    """Lee entrada/triunfobet_AAAAMMDD.csv; si no existe, crea la plantilla."""
    f = archivo_cuotas(fecha)
    if not f.exists():
        guardar_cuotas(fecha, {g["partido"]: {} for g in (juegos or [])}, juegos)
        return {}
    res = {}
    with f.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            res[r["partido"].strip()] = {k: float(str(v).replace(",", ".")) for k, v in r.items()
                                         if k != "partido" and v and str(v).strip()}
    return res


def guardar_cuotas(fecha, datos, juegos=None):
    lineas = {g["partido"]: g["dk"]["total"] for g in (juegos or [])}
    with archivo_cuotas(fecha).open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(CAMPOS)
        for p, d in datos.items():
            t = lineas.get(p)
            fila = [p] + [d.get(k, "") for k in CAMPOS[1:]]
            if t and not d:  # plantilla nueva: sugerir líneas alternativas
                fila[3], fila[5] = t + M.COLCHON, t - M.COLCHON
            w.writerow(fila)


def analizar(fecha, log=print):
    juegos = F.partidos(fecha)
    if not juegos:
        return dict(fecha=str(fecha), partidos=[], lg={})
    temporada = fecha.year + 1 if fecha.month >= 9 else fecha.year
    les, stats, mins = F.lesiones(), F.estadisticas(temporada), F.minutos_jugadores(temporada)
    lg = stats.get("League Average")
    casa = leer_cuotas(fecha, juegos)

    # Guardar/restaurar cuotas pre-partido para que ESPN no borre el partido al iniciar
    odds_file = F.CACHE / f"odds_{fecha:%Y%m%d}.json"
    saved_odds = json.loads(odds_file.read_text(encoding="utf-8")) if odds_file.exists() else {}
    changed_odds = False
    for g in juegos:
        gid = g["id"]
        if g["dk"].get("total") is not None:
            saved_odds[gid] = g["dk"]
            changed_odds = True
        elif gid in saved_odds:
            g["dk"] = saved_odds[gid]
    if changed_odds:
        odds_file.write_text(json.dumps(saved_odds), encoding="utf-8")

    analisis = []
    for g in juegos:
        sh, sa = F.stats_de(stats, g["home"]["nombre"]), F.stats_de(stats, g["away"]["nombre"])
        if not sh or not sa or not g["dk"]["total"]:
            log(f"  ! {g['partido']}: faltan stats o línea, se omite"); continue
        l5h, l5a = F.ultimos5(g["home"]["id"]), F.ultimos5(g["away"]["id"])
        oh, gh, mh = M.clasificar_lesiones(les.get(g["home"]["id"], []), mins)
        oa, ga, ma = M.clasificar_lesiones(les.get(g["away"]["id"], []), mins)
        gtd = gh + ga
        dh, b2b_h, t34_h = M.dias_descanso(l5h, fecha)
        da, b2b_a, t34_a = M.dias_descanso(l5a, fecha)
        pen_h, pen_a = M.impacto_out(oh), M.impacto_out(oa)
        proy = M.proyectar(sh, sa, l5h, l5a, lg, b2b_h=b2b_h, b2b_a=b2b_a, out_h=pen_h, out_a=pen_a)
        cc = casa.get(g["partido"], {})
        linea, spread = g["dk"]["total"], g["dk"]["spread_home"]

        # Discrepancia modelo vs mercado
        disc_m = round(proy["margen"] - (-spread), 2) if spread is not None else 0
        disc_t = round(proy["total"] - linea, 2)
        alerta_lado = f"BLOQUEADO DISCREPANCIA MERCADO (modelo {proy['margen']:+.1f} vs spread {-spread:+.1f})" \
            if abs(disc_m) > M.DISC_MARGEN else None
        alerta_total = f"AVISO modelo difiere {disc_t:+.1f} pts de la línea" if abs(disc_t) > M.DISC_TOTAL else None

        sel = []
        lado = M.evaluar_lado(g, proy, cc)
        lado.update(tipo="Ganador (P+)", estado=M.estado("lado", linea, gtd, lado["cuota"], disc=alerta_lado))
        sel.append(lado)
        for tipo in ("under", "over"):
            c_manual = cc.get(f"{tipo}_cuota")
            l_manual = cc.get(f"{tipo}_linea") if c_manual else None  # Fix: no congelar línea vieja del CSV
            r = M.evaluar_total(tipo, linea, proy, sh, sa, lg, c_manual, l_manual)
            r.update(tipo=f"Total Alt. {tipo.upper()}",
                     estado=M.estado(tipo, linea, gtd, r["cuota"], r["cuota_est"], disc=alerta_total))
            sel.append(r)

        # Fase D: Alertas de movimiento brusco de línea (Steam Moves >= 1.5 pts)
        steam = []
        t_open, s_open = g["dk"].get("total_open"), g["dk"].get("spread_open")
        if t_open is not None and abs(linea - t_open) >= 1.5:
            steam.append(f"⚡ STEAM Total {linea - t_open:+.1f} pts ({t_open}→{linea})")
        if s_open is not None and spread is not None and abs(spread - s_open) >= 1.5:
            steam.append(f"⚡ STEAM Spread {spread - s_open:+.1f} pts ({s_open}→{spread})")

        props = M.sugerir_props(g, proy, mins, oh + oa, gtd, lg)
        analisis.append(dict(g=g, sh=sh, sa=sa, l5h=l5h, l5a=l5a, out=oh + oa, gtd=gtd, gtd_menor=mh + ma,
                             proy=proy, sel=sel, props=props, casa=cc, cuarentena=bool(gtd),
                             disc_margen=disc_m, disc_total=disc_t, steam=steam,
                             descanso=dict(home=dh, away=da, b2b_h=b2b_h, b2b_a=b2b_a, t34_h=t34_h, t34_a=t34_a)))
        log(f"  {g['hora']}  {g['partido']:<11} modelo {proy['total']:.2f} | línea {linea} | "
            f"GTD clave {len(gtd)} (menor {len(mh + ma)}) | disc. margen {disc_m:+.1f}")
    return dict(fecha=str(fecha), partidos=analisis, lg=lg)


def generar(fecha, log=print):
    res = analizar(fecha, log)
    REPORTES.mkdir(exist_ok=True)
    out = REPORTES / f"NBA_Parley_{fecha}.xlsx"
    if res["partidos"]:
        H.registrar_jornada(fecha, res["partidos"])
        generar_excel(res["partidos"], fecha, res["lg"], out)
        log(f"Excel generado: {out}")
    return res, out


if __name__ == "__main__":
    f = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.datetime.now(F.VET).date()
    print(f"Jornada {f} - descargando...")
    generar(f)
