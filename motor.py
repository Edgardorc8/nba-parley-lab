"""Motor cuantitativo 65/35 + Z-score doble escenario + reglas de riesgo."""
from statistics import NormalDist

N = NormalDist()
SIGMA = 18.0          # desviación de totales NBA
SIGMA_MARGEN = 12.5   # desviación del margen final
COLCHON = 7.5
LOW_LINE = 225.5
ADJ_CAP = 0.03
ODDS_MIN, ODDS_MAX = 1.15, 1.55
CUOTA_ALT_EST = 1.42  # referencia de mercado para total alternativo ±7.5 pts (con vig ~5%)
GTD_ESTADOS = ("day-to-day", "questionable", "doubtful", "game-time", "gtd")


def _avg(lst, k):
    return sum(x[k] for x in lst) / len(lst) if lst else 0.0


GTD_MIN_MPG = 24.0      # GTD con 24+ min o titular habitual -> cuarentena
DISC_MARGEN = 5.0       # alerta si modelo y spread difieren más de 5 pts
DISC_TOTAL = 8.0
HCA_PTS = 2.5           # ventaja de localía NBA (+1.25 local, -1.25 visita)
B2B_LOCAL = -2.0        # penalización Back-to-Back en casa
B2B_VISITA = -3.0       # penalización Back-to-Back de visita


def clasificar_lesiones(lst, mins):
    """Devuelve (OUT, GTD clave que bloquea, GTD menor que no bloquea)."""
    from fuentes import norm
    out, clave, menor = [], [], []
    for x in lst:
        m = mins.get(norm(x["jugador"]), {})
        x = dict(x, mpg=m.get("mpg", 0), titular=m.get("gs_ratio", 0) >= 0.5, bpm=m.get("bpm", 0.0))
        e = x["estado"].lower()
        if e == "out":
            out.append(x)
        elif any(s in e for s in GTD_ESTADOS):
            (clave if x["mpg"] >= GTD_MIN_MPG or x["titular"] else menor).append(x)
    return out, clave, menor


def dias_descanso(l5, fecha):
    """Días desde el último juego (1 = B2B) y si viene en 3-en-4 noches."""
    import datetime as dt
    if not l5:
        return None, False, False
    fechas = [dt.date.fromisoformat(x["fecha"]) for x in l5]
    dias = (fecha - fechas[-1]).days
    b2b = dias == 1
    tres_en_cuatro = sum(1 for d in fechas if 1 <= (fecha - d).days <= 3) >= 2
    return dias, b2b, tres_en_cuatro


def impacto_out(out_lst):
    """Estima cuántos puntos netos pierde un equipo por sus bajas OUT (usando BPM y minutos)."""
    tot = 0.0
    for x in out_lst:
        if x["mpg"] >= 18 and x["bpm"] > 0:
            tot += min(4.5, x["bpm"] * (x["mpg"] / 48.0) * 0.65)
    return round(min(7.5, tot), 2)


def proyectar(sh, sa, l5h, l5a, lg, b2b_h=False, b2b_a=False, out_h=0.0, out_a=0.0):
    pace = sh["pace"] * sa["pace"] / lg["pace"]
    mh = pace / 100 * sh["ortg"] * sa["drtg"] / lg["ortg"]
    ma = pace / 100 * sa["ortg"] * sh["drtg"] / lg["ortg"]
    l5_h = (_avg(l5h, "pf48") + _avg(l5a, "pa48")) / 2
    l5_a = (_avg(l5a, "pf48") + _avg(l5h, "pa48")) / 2
    ch = 0.65 * mh + 0.35 * l5_h
    ca = 0.65 * ma + 0.35 * l5_a

    # Ajustes Fase A + Fix Auditoría: Localía (HCA) + B2B (margen -2/-3, total -1 por equipo) + Bajas OUT
    ch += HCA_PTS / 2 - out_h * 0.65 + out_a * 0.35
    ca += -HCA_PTS / 2 - out_a * 0.65 + out_h * 0.35
    if b2b_h:
        ch -= 1.5; ca += 0.5
    if b2b_a:
        ca -= 2.0; ch += 1.0
    sigma_dyn = round(max(16.2, min(19.8, SIGMA + (pace - lg["pace"]) * 0.35)), 2)

    r = dict(pace=pace, mult_h=mh, mult_a=ma, mult_total=mh + ma, l5_h=l5_h, l5_a=l5_a,
             l5_total=l5_h + l5_a, cal_h=ch, cal_a=ca, sigma=sigma_dyn,
             out_pen_h=out_h, out_pen_a=out_a)
    r = {k: round(v, 2) for k, v in r.items()}          # fuente única de verdad al centésimo
    r["total"] = round(r["cal_h"] + r["cal_a"], 2)
    r["margen"] = round(r["cal_h"] - r["cal_a"], 2)
    return r


def ajuste_situacional(lado, sh, sa, lg, margen):
    """Ajuste explícito y acotado a ±3% (positivo favorece la selección)."""
    a, motivos = 0.0, []
    lento = sh["pace"] < lg["pace"] and sa["pace"] < lg["pace"]
    rapido = sh["pace"] > lg["pace"] and sa["pace"] > lg["pace"]
    ft = (sh["fta"] + sa["fta"] + sh["opp_fta"] + sa["opp_fta"]) / 2
    ft_lg = lg["fta"] * 2
    if lado == "under":
        if lento: a += 0.01; motivos.append("ambos pace bajo liga")
        if rapido: a -= 0.01; motivos.append("ambos pace alto")
        if ft > ft_lg + 3: a -= 0.01; motivos.append("muchos TL")
        if ft < ft_lg - 3: a += 0.01; motivos.append("pocos TL")
        if abs(margen) >= 12: a += 0.01; motivos.append("posible paliza/banca 4Q")
    else:
        if rapido: a += 0.01; motivos.append("ambos pace alto")
        if lento: a -= 0.01; motivos.append("ambos pace bajo liga")
        if ft > ft_lg + 3: a += 0.01; motivos.append("muchos TL")
        if ft < ft_lg - 3: a -= 0.01; motivos.append("pocos TL")
        if abs(margen) >= 12: a -= 0.01; motivos.append("riesgo banca 4Q")
    return max(-ADJ_CAP, min(ADJ_CAP, a)), ", ".join(motivos) or "neutral"


def evaluar_total(lado, linea_mercado, proy, sh, sa, lg, cuota=None, linea_alt=None):
    alt = linea_alt or (linea_mercado + COLCHON if lado == "under" else linea_mercado - COLCHON)
    d1 = alt - proy["total"] if lado == "under" else proy["total"] - alt
    d2 = alt - proy["l5_total"] if lado == "under" else proy["l5_total"] - alt
    adj, motivo = ajuste_situacional(lado, sh, sa, lg, proy["margen"])
    sig = proy.get("sigma", SIGMA)
    z1, z2 = d1 / sig, d2 / sig
    p1, p2 = N.cdf(z1) + adj, N.cdf(z2) + adj
    est = cuota is None
    cuota = cuota or CUOTA_ALT_EST
    prob = min(p1, p2)  # conservador: el peor de los dos escenarios
    return dict(seleccion=f"{'Under' if lado == 'under' else 'Over'} {alt}", linea_alt=alt,
                d1=round(d1, 2), d2=round(d2, 2), z1=round(z1, 3), z2=round(z2, 3),
                base1=N.cdf(z1), base2=N.cdf(z2), adj=adj, motivo=motivo, p1=p1, p2=p2,
                prob=prob, cuota=cuota, cuota_est=est, implicita=1 / cuota, ventaja=prob - 1 / cuota)


def devig_ml(ml_h, ml_a):
    """Quita el margen de la casa (vig) a las cuotas ML para obtener la probabilidad limpia del mercado."""
    if not ml_h or not ml_a or ml_h <= 1 or ml_a <= 1:
        return None, None, None
    ih, ia = 1 / ml_h, 1 / ml_a
    tot = ih + ia
    return round(ih / tot, 4), round(ia / tot, 4), round(tot - 1, 4)


def evaluar_lado(g, proy, cuotas_casa):
    fav_home = proy["margen"] >= 0
    t = g["home"] if fav_home else g["away"]
    p = N.cdf(abs(proy["margen"]) / SIGMA_MARGEN)
    cuota = cuotas_casa.get("ml_local" if fav_home else "ml_visita") or \
        (g["dk"]["ml_home"] if fav_home else g["dk"]["ml_away"])
    fuente = "casa" if cuotas_casa.get("ml_local" if fav_home else "ml_visita") else "DK"
    cuota = cuota or 1.0
    nvh, nva, vig = devig_ml(g["dk"].get("ml_home"), g["dk"].get("ml_away"))
    prob_novig = nvh if fav_home else nva
    return dict(seleccion=f"{t['nombre']} Ganador (P+)", prob=p, cuota=cuota, fuente=fuente,
                implicita=1 / cuota if cuota > 1 else 1, ventaja=p - (1 / cuota if cuota > 1 else 1),
                prob_novig=prob_novig, vig=vig)


def estado(tipo, linea_mercado, gtd, cuota, cuota_est=False, disc=None):
    st = []
    if disc:
        st.append(disc)
    if tipo == "over" and linea_mercado is not None and linea_mercado <= LOW_LINE:
        st.append("BLOQUEADO POR LÍNEA BAJA <= 225.5")
    if gtd:
        st.append("EN CUARENTENA GTD (" + ", ".join(x["jugador"] for x in gtd[:4]) + ")")
    if cuota and not (ODDS_MIN <= cuota <= ODDS_MAX):
        st.append(f"AVISO cuota fuera de {ODDS_MIN}-{ODDS_MAX}")
    return " | ".join(st) if st else "LUZ VERDE"


def bloqueado(s):
    return "BLOQUEADO" in s or "CUARENTENA" in s


def sugerir_props(g, proy, mins, out_lst, gtd_lst, lg):
    """Player Props protegidos en 5 categorías (Puntos, Rebotes, Asistencias, R+A, PRA) con Filtro Anti-Garbage Time."""
    from math import floor
    from fuentes import ESPN_TO_BBREF_ABBR, norm
    spread = abs(g["dk"].get("spread_home") or 0)
    garbage_risk = spread >= 11.5
    fuera = {norm(x["jugador"]) for x in out_lst + gtd_lst}
    pace_f = proy["pace"] / lg["pace"]
    candidatos = []
    for side in ("home", "away"):
        abbr_espn = g[side]["abbr"]
        abbr_bb = ESPN_TO_BBREF_ABBR.get(abbr_espn, abbr_espn)
        for n, p in mins.items():
            if p["equipo"] != abbr_bb or n in fuera or p["mpg"] < 28.0:
                continue
            ppg, rpg, apg = p["ppg"] * pace_f, p["rpg"] * pace_f, p["apg"] * pace_f
            ra, pra = rpg + apg, ppg + rpg + apg
            base = dict(partido=g["partido"], hora=g["hora"], equipo=abbr_espn, jugador=p["nombre"],
                        cuota=1.55, cuarentena=bool(gtd_lst))
            f_txt = "Anti-Garbage Time (spread >=11.5)" if garbage_risk else "Minutos asegurados (duelo cerrado)"

            if rpg >= 7.0:
                ln = floor(rpg - 1.0) + 0.5
                pr = N.cdf((rpg - ln) / 3.2) - (0.03 if garbage_risk else 0.0)
                candidatos.append(dict(base, cat="REBOTES", prop=f"Over {ln} Rebotes",
                                       promedio=f"{p['rpg']:.1f} RPG ({p['mpg']:.1f}m)", prob=round(pr, 4), filtro=f_txt))
            if apg >= 5.5:
                ln = floor(apg - 0.8) + 0.5
                pr = N.cdf((apg - ln) / 2.5) - (0.03 if garbage_risk else 0.0)
                candidatos.append(dict(base, cat="ASISTENCIAS", prop=f"Over {ln} Asistencias",
                                       promedio=f"{p['apg']:.1f} APG ({p['mpg']:.1f}m)", prob=round(pr, 4), filtro=f_txt))
            if ra >= 11.5:
                ln = floor(ra - 1.5) + 0.5
                pr = N.cdf((ra - ln) / 4.0) - (0.03 if garbage_risk else 0.0)
                candidatos.append(dict(base, cat="R+A", prop=f"Over {ln} Reb + Ast",
                                       promedio=f"{p['rpg']+p['apg']:.1f} R+A ({p['mpg']:.1f}m)", prob=round(pr, 4), filtro=f_txt))
            if not garbage_risk:
                if ppg >= 18.0:
                    ln = floor(ppg - 2.5) + 0.5
                    pr = N.cdf((ppg - ln) / 6.5)
                    candidatos.append(dict(base, cat="PUNTOS", prop=f"Over {ln} Puntos",
                                           promedio=f"{p['ppg']:.1f} PPG ({p['mpg']:.1f}m)", prob=round(pr, 4), filtro=f_txt))
                if pra >= 28.0:
                    ln = floor(pra - 3.0) + 0.5
                    pr = N.cdf((pra - ln) / 8.0)
                    candidatos.append(dict(base, cat="PRA", prop=f"Over {ln} PRA",
                                           promedio=f"{p['ppg']+p['rpg']+p['apg']:.1f} PRA ({p['mpg']:.1f}m)", prob=round(pr, 4), filtro=f_txt))
    candidatos.sort(key=lambda x: -x["prob"])
    return candidatos
