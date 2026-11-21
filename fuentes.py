"""Fuentes gratuitas: ESPN (partidos, cuotas DraftKings, lesiones, L5) y Basketball-Reference (métricas avanzadas)."""
import datetime as dt
import json
import re
import time
import urllib.request
from pathlib import Path

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (uso personal; NBA Parley Lab)"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
VET = dt.timezone(dt.timedelta(hours=-4))
BBREF_FIX = {"LA Clippers": "Los Angeles Clippers"}


def http(url, ttl=1800, raw=False):
    """GET con caché en disco, reintentos y pausa cortés entre peticiones."""
    key = CACHE / (re.sub(r"\W", "_", url)[-150:] + ".cache")
    if key.exists() and time.time() - key.stat().st_mtime < ttl:
        txt = key.read_text(encoding="utf-8")
    else:
        for i in range(3):
            try:
                req = urllib.request.Request(url, headers=UA)
                txt = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
                break
            except Exception:
                if i == 2:
                    raise
                time.sleep(4)
        key.write_text(txt, encoding="utf-8")
        time.sleep(1.0)
    return txt if raw else json.loads(txt)


def am2dec(s):
    try:
        a = float(str(s).replace("+", ""))
    except (TypeError, ValueError):
        return None
    return round(1 + (a / 100 if a > 0 else 100 / -a), 3)


def _num(x):
    m = re.search(r"-?\d*\.?\d+", str(x or ""))
    return float(m.group()) if m else None


# ---------------- ESPN ----------------
def partidos(fecha):
    s = http(f"{ESPN}/scoreboard?dates={fecha:%Y%m%d}", ttl=900)
    out = []
    for e in s.get("events", []):
        c = e["competitions"][0]
        teams = {x["homeAway"]: x for x in c["competitors"]}
        hora = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(VET)
        g = dict(id=e["id"], hora=hora.strftime("%I:%M %p"), partido=e["shortName"],
                 estado=c["status"]["type"]["description"])
        for side in ("home", "away"):
            t = teams[side]["team"]
            rec = {r.get("name", r.get("type", "")).lower(): r.get("summary") for r in teams[side].get("records", [])}
            g[side] = dict(id=t["id"], abbr=t["abbreviation"], nombre=t["displayName"],
                           logo=t.get("logo", ""),
                           record=rec.get("overall") or rec.get("total") or "",
                           rec_casa=rec.get("home", ""), rec_visita=rec.get("road", rec.get("away", "")))
        o = (c.get("odds") or [{}])[0]
        ml, tot, ps = o.get("moneyline", {}), o.get("total", {}), o.get("pointSpread", {})
        g["dk"] = dict(
            total=o.get("overUnder"),
            total_open=_num(tot.get("over", {}).get("open", {}).get("line")),
            spread_home=o.get("spread"),
            spread_open=_num(ps.get("home", {}).get("open", {}).get("line")),
            ml_home=am2dec(ml.get("home", {}).get("close", {}).get("odds")),
            ml_away=am2dec(ml.get("away", {}).get("close", {}).get("odds")),
            ml_home_open=am2dec(ml.get("home", {}).get("open", {}).get("odds")),
        )
        out.append(g)
    return out


def lesiones():
    d = http(f"{ESPN}/injuries", ttl=900)
    res = {}
    for t in d.get("injuries", []):
        res[str(t["id"])] = [dict(jugador=i.get("athlete", {}).get("displayName", "?"),
                                  estado=i.get("status", ""),
                                  detalle=(i.get("shortComment") or "")[:160])
                             for i in t.get("injuries", [])]
    return res


def ultimos5(team_id):
    d = http(f"{ESPN}/teams/{team_id}/schedule", ttl=3600)
    hechos = [e for e in d.get("events", []) if e["competitions"][0]["status"]["type"].get("completed")]
    out = []
    for e in hechos[-5:]:
        c = e["competitions"][0]
        me = next(x for x in c["competitors"] if x["team"]["id"] == str(team_id))
        op = next(x for x in c["competitors"] if x is not me)
        pf, pa = float(me["score"]["value"]), float(op["score"]["value"])
        period = int(c["status"].get("period", 4) or 4)
        pf48, pa48 = pf, pa
        if period > 4:  # prórroga: reconstruir marcador de 48 min
            sm = http(f"{ESPN}/summary?event={e['id']}", ttl=86400 * 30)
            for x in sm["header"]["competitions"][0]["competitors"]:
                reg = sum(float(l.get("displayValue", 0) or 0) for l in x.get("linescores", [])[:4])
                if x["team"]["id"] == str(team_id):
                    pf48 = reg
                else:
                    pa48 = reg
        out.append(dict(fecha=e["date"][:10], rival=op["team"]["abbreviation"],
                        cond="Local" if me["homeAway"] == "home" else "Visita",
                        pf=pf, pa=pa, pf48=pf48, pa48=pa48, ot=max(0, period - 4)))
    return out


# ---------------- Basketball-Reference ----------------
def _tabla(html, tid):
    i = html.find(f'id="{tid}"')
    if i < 0:
        return {}
    t = html[i:html.find("</table>", i)]
    rows = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        cells = {k: re.sub(r"<[^>]+>", "", v).strip()
                 for k, v in re.findall(r'data-stat="([^"]+)"[^>]*>(.*?)</t[dh]>', tr, re.S)}
        name = (cells.get("team") or cells.get("team_name") or "").replace("*", "").strip()
        if name and name != "Team":
            rows[name] = {k: (_num(v) if k not in ("team", "team_name") else v) for k, v in cells.items()}
    return rows


def estadisticas(temporada=2027):
    h = http(f"https://www.basketball-reference.com/leagues/NBA_{temporada}.html", ttl=6 * 3600, raw=True)
    h = h.replace("<!--", "").replace("-->", "")
    adv = _tabla(h, "advanced-team")
    pg = _tabla(h, "per_game-team")
    op = _tabla(h, "per_game-opponent")
    res = {}
    for name, a in adv.items():
        p, o = pg.get(name, {}), op.get(name, {})
        res[name] = dict(
            ortg=a.get("off_rtg"), drtg=a.get("def_rtg"), pace=a.get("pace"),
            opp_efg=a.get("opp_efg_pct"), tov=a.get("tov_pct"), drb=a.get("drb_pct"),
            ppg=p.get("pts"), fta=p.get("fta"),
            opp_ppg=o.get("opp_pts", o.get("pts")), opp_fta=o.get("opp_fta", o.get("fta")),
            opp_3p=o.get("opp_fg3_pct", o.get("fg3_pct")),
        )
    return res


def stats_de(stats, nombre_espn):
    return stats.get(BBREF_FIX.get(nombre_espn, nombre_espn), {})


def norm(nombre):
    import unicodedata
    s = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).replace(" jr", "").replace(" iii", "").replace(" ii", "").strip()


ESPN_TO_BBREF_ABBR = {
    "NY": "NYK", "SA": "SAS", "NO": "NOP", "GS": "GSW", "UTAH": "UTA", "WSH": "WAS",
    "PHX": "PHO", "BKN": "BRK", "CHA": "CHO",
}


def minutos_jugadores(temporada=2027):
    """{nombre_normalizado: dict(nombre, mpg, gs_ratio, equipo, bpm, ppg, rpg, apg)} desde Basketball-Reference."""
    h = http(f"https://www.basketball-reference.com/leagues/NBA_{temporada}_per_game.html", ttl=6 * 3600, raw=True)
    res = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h[h.find("<tbody"):], re.S):
        c = {k: re.sub(r"<[^>]+>", "", v).strip()
             for k, v in re.findall(r'data-stat="([^"]+)"[^>]*>(.*?)</t[dh]>', tr, re.S)}
        raw_name = c.get("name_display", "")
        n = norm(raw_name)
        if not n or n in res:  # primera fila = total si jugó en 2 equipos
            continue
        g = _num(c.get("games")) or 0
        res[n] = dict(
            nombre=raw_name, mpg=_num(c.get("mp_per_g")) or 0,
            gs_ratio=(_num(c.get("games_started")) or 0) / g if g else 0,
            equipo=c.get("team_name_abbr"), bpm=0.0,
            ppg=_num(c.get("pts_per_g")) or 0, rpg=_num(c.get("trb_per_g")) or 0, apg=_num(c.get("ast_per_g")) or 0,
        )
    try:
        ha = http(f"https://www.basketball-reference.com/leagues/NBA_{temporada}_advanced.html", ttl=6 * 3600, raw=True)
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", ha[ha.find("<tbody"):], re.S):
            c = {k: re.sub(r"<[^>]+>", "", v).strip()
                 for k, v in re.findall(r'data-stat="([^"]+)"[^>]*>(.*?)</t[dh]>', tr, re.S)}
            n = norm(c.get("name_display"))
            if n in res and res[n]["bpm"] == 0.0:
                res[n]["bpm"] = _num(c.get("bpm")) or 0.0
    except Exception:
        pass
    return res
