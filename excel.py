"""Generación del Excel con el mismo estilo de excel-parley.py."""
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

import motor as M

NAVY, SUB, ZEBRA = "1B365D", "2C5282", "F4F7FA"
F_T = Font(size=14, bold=True, color="FFFFFF")
F_H = Font(size=10, bold=True, color="FFFFFF")
F_C = Font(size=10, color="0F172A")
FILL = lambda c: PatternFill(start_color=c, end_color=c, fill_type="solid")
BORDER = Border(*(Side(style="thin", color="CBD5E1"),) * 4)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
COLOR_ESTADO = {"LUZ": "D1FAE5", "AVISO": "FEF3C7", "BLOQ": "FEE2E2", "EN C": "FEE2E2"}


def hoja(wb, nombre, titulo, headers, filas, anchos, pct=(), dec=()):
    ws = wb.create_sheet(nombre)
    n = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n)
    c = ws.cell(1, 1, titulo); c.font = F_T; c.fill = FILL(NAVY)
    ws.row_dimensions[1].height = 28
    for i, h in enumerate(headers, 1):
        c = ws.cell(3, i, h); c.font = F_H; c.fill = FILL(SUB); c.alignment = CENTER; c.border = BORDER
    for r, fila in enumerate(filas, 4):
        for i, v in enumerate(fila, 1):
            c = ws.cell(r, i, v); c.font = F_C; c.border = BORDER; c.alignment = CENTER
            if r % 2 == 0: c.fill = FILL(ZEBRA)
            if i in pct: c.number_format = "0.0%"
            elif i in dec: c.number_format = "0.00"
            if isinstance(v, str) and v[:4] in COLOR_ESTADO or (isinstance(v, str) and v[:4] == "LUZ "):
                c.fill = FILL(COLOR_ESTADO.get(v[:4], "FFFFFF"))
    for i, w in enumerate(anchos, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)].width = w
    ws.freeze_panes = "A4"
    if filas:
        ws.auto_filter.ref = f"A3:{chr(64 + n)}{3 + len(filas)}"
    return ws


def generar_excel(analisis, fecha, lg, path):
    wb = Workbook(); wb.remove(wb.active)
    todas = []
    for a in analisis:
        for s in a["sel"]:
            todas.append((a, s))
    eleg = sorted([x for x in todas if not M.bloqueado(x[1]["estado"])], key=lambda x: -x[1]["prob"])

    # Resumen: selecciones elegibles por probabilidad (máx. 1 por partido en el top)
    vistos, filas = set(), []
    for a, s in eleg:
        top = "★ TOP (1 por partido)" if a["g"]["partido"] not in vistos else ""
        vistos.add(a["g"]["partido"])
        filas.append([a["g"]["hora"], a["g"]["partido"], s["tipo"], s["seleccion"], s["cuota"],
                      s["prob"], s["implicita"], s["ventaja"], top, s["estado"]])
    hoja(wb, "Resumen", f"NBA PARLEY LAB — SELECCIONES ELEGIBLES {fecha} (VET) | σ={M.SIGMA}",
         ["Hora VET", "Partido", "Mercado", "Selección", "Cuota", "Prob. Real", "Prob. Implícita",
          "Ventaja", "Combinado", "Estado"], filas, [10, 12, 18, 30, 8, 11, 12, 10, 20, 50],
         pct=(6, 7, 8), dec=(5,))

    # Ficha técnica
    filas = []
    for a in analisis:
        g, p, sh, sa, dk = a["g"], a["proy"], a["sh"], a["sa"], a["g"]["dk"]
        mov_t = f"{dk['total_open']} → {dk['total']}" if dk["total_open"] else dk["total"]
        mov_s = f"{dk['spread_open']} → {dk['spread_home']}" if dk["spread_open"] is not None else dk["spread_home"]
        filas.append([g["hora"], g["partido"], f"{g['away']['record']} (V {g['away']['rec_visita']})",
                      f"{g['home']['record']} (L {g['home']['rec_casa']})", dk["ml_away"], dk["ml_home"],
                      mov_s, mov_t, a["casa"].get("ml_visita"), a["casa"].get("ml_local"),
                      f"{sa['ortg']}/{sa['drtg']}/{sa['pace']}", f"{sh['ortg']}/{sh['drtg']}/{sh['pace']}",
                      p["pace"], p["mult_a"], p["mult_h"], p["l5_a"], p["l5_h"], p["cal_a"], p["cal_h"],
                      p["total"], p["l5_total"], p["margen"],
                      "EN CUARENTENA GTD" if a["cuarentena"] else "LUZ VERDE"])
    hoja(wb, "Ficha_Modelo", "FICHA TÉCNICA + MOTOR 65/35 (puntos al centésimo, fuente única)",
         ["Hora", "Partido", "Visita récord", "Local récord", "DK ML Vis", "DK ML Loc", "Spread local (apertura→actual)",
          "Total (apertura→actual)", "Casa ML Vis", "Casa ML Loc", "Vis ORtg/DRtg/Pace", "Loc ORtg/DRtg/Pace",
          "Pace proy.", "Mult Vis", "Mult Loc", "L5 Vis", "L5 Loc", "Calib. Vis", "Calib. Loc",
          "TOTAL CALIBRADO", "Total L5", "Margen (Loc)", "Estado"],
         filas, [9, 11, 15, 15, 8, 8, 14, 14, 8, 8, 16, 16] + [9] * 10 + [20], dec=tuple(range(13, 23)))

    # Totales Z-score doble escenario
    filas = []
    for a in analisis:
        for s in a["sel"]:
            if not s["tipo"].startswith("Total"):
                continue
            filas.append([a["g"]["partido"], a["g"]["dk"]["total"], s["seleccion"], a["proy"]["total"],
                          a["proy"]["l5_total"], s["d1"], s["d2"], s["z1"], s["z2"], s["base1"], s["base2"],
                          s["adj"], s["motivo"], s["p1"], s["p2"],
                          f"{s['cuota']}{' (est.)' if s['cuota_est'] else ''}", s["implicita"], s["ventaja"], s["estado"]])
    ws = hoja(wb, "Totales_ZScore", f"TOTALES ALTERNATIVOS — Z-SCORE DOBLE ESCENARIO (σ={M.SIGMA}, colchón ±{M.COLCHON})",
              ["Partido", "Línea", "Selección", "Modelo 65/35", "Entorno L5", "Δ1", "Δ2", "Z1", "Z2", "Φ(Z1)", "Φ(Z2)",
               "Ajuste", "Motivo ajuste", "Prob. Esc.1", "Prob. Esc.2", "Cuota", "Implícita", "Ventaja (peor esc.)", "Estado"],
              filas, [11, 8, 14, 10, 10, 7, 7, 7, 7, 8, 8, 8, 26, 9, 9, 10, 9, 10, 50],
              pct=(10, 11, 12, 14, 15, 17, 18), dec=(4, 5, 6, 7))
    if filas:
        ws.conditional_formatting.add(f"N4:O{3 + len(filas)}", ColorScaleRule(
            start_type="num", start_value=0.55, start_color="FEE2E2", mid_type="num", mid_value=0.68,
            mid_color="FEF3C7", end_type="num", end_value=0.80, end_color="D1FAE5"))

    # L5
    filas = []
    for a in analisis:
        for lado, l5 in (("away", a["l5a"]), ("home", a["l5h"])):
            for x in l5:
                filas.append([a["g"]["partido"], a["g"][lado]["abbr"], x["fecha"], x["cond"], x["rival"],
                              f"{int(x['pf'])}-{int(x['pa'])}", int(x["pf"] + x["pa"]),
                              f"OT{x['ot']}: 48m {int(x['pf48'])}-{int(x['pa48'])}" if x["ot"] else "—"])
    hoja(wb, "L5_Marcadores", "ÚLTIMOS 5 JUEGOS (marcador final y 48 min si hubo prórroga)",
         ["Partido", "Equipo", "Fecha", "Cond.", "Rival", "Marcador", "Total", "Prórroga"], filas,
         [11, 8, 11, 8, 8, 10, 7, 22])

    # Lesiones
    filas = []
    for a in analisis:
        for x in a["out"]:
            filas.append([a["g"]["partido"], x["jugador"], "OUT", x["detalle"]])
        for x in a["gtd"]:
            filas.append([a["g"]["partido"], x["jugador"], f"GTD CLAVE ({x['estado']}, {x['mpg']} min)", x["detalle"]])
        for x in a["gtd_menor"]:
            filas.append([a["g"]["partido"], x["jugador"], f"GTD menor ({x['estado']}, {x['mpg']} min)", x["detalle"]])
    hoja(wb, "Lesiones", "INJURY REPORT (ESPN) — OUT vs GTD", ["Partido", "Jugador", "Estado", "Detalle"],
         filas, [11, 24, 20, 90])
    wb.save(path)
