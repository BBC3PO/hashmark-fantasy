#!/usr/bin/env python3
"""Hashmark Fantasy data refresh: pulls Sleeper + ESPN public data and writes data.json.

Usage: python3 -I refresh.py OUT_JSON [LEAGUE_ID ...]
Needs network access to api.sleeper.app, api.sleeper.com, site.api.espn.com,
lm-api-reads.fantasy.espn.com.
"""
import json, sys, os, re, datetime, urllib.request, urllib.error, gzip, time
from concurrent.futures import ThreadPoolExecutor

OUT = sys.argv[1]
LEAGUE_IDS = [x for x in sys.argv[2:] if re.fullmatch(r"\d{3,12}", x)]
POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
ESPN_FIX = {"WSH": "WAS"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; HashmarkFantasy/1.0)", "Accept-Encoding": "gzip"}


def get(url, headers=None, tries=3):
    h = dict(UA)
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    b = gzip.decompress(b)
                return json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise
            time.sleep(2 * (i + 1))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError("failed " + url)


def pmap(fn, items, n=12):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))


state = get("https://api.sleeper.app/v1/state/nfl")
SEASON = int(state["season"])
CUR = max(1, min(18, int(state.get("display_week") or state["week"])))
NEXT = min(18, CUR + 1)
print("season", SEASON, "week", CUR)
PQ = "&".join("position[]=" + p for p in POS)

players = get("https://api.sleeper.app/v1/players/nfl")
proj_raw = {w: get(f"https://api.sleeper.com/projections/nfl/{SEASON}/{w}?season_type=regular&{PQ}") for w in sorted({CUR, NEXT})}
pseason_raw = get(f"https://api.sleeper.com/projections/nfl/{SEASON}?season_type=regular&{PQ}")
sseason_raw = get(f"https://api.sleeper.com/stats/nfl/{SEASON}?season_type=regular&{PQ}")
weekly_raw = dict(zip(range(1, CUR + 1), pmap(lambda w: get(f"https://api.sleeper.com/stats/nfl/{SEASON}/{w}?season_type=regular&{PQ}"), range(1, CUR + 1))))
trend_add = get("https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=48&limit=60")
trend_drop = get("https://api.sleeper.app/v1/players/nfl/trending/drop?lookback_hours=48&limit=60")
FILTER = {"players": {"filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]}, "filterStatsForTopScoringPeriodIds": {"value": 5, "additionalValue": [f"00{SEASON}", f"10{SEASON}", f"11{SEASON}{CUR}", f"11{SEASON}{NEXT}"]},
          "limit": 1000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}, "filterActive": {"value": True}}}
espn = get(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leaguedefaults/3?scoringPeriodId={CUR}&view=kona_player_info",
           {"X-Fantasy-Filter": json.dumps(FILTER)})["players"]
sbs = dict(zip(range(1, 19), pmap(lambda w: get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week={w}&dates={SEASON}"), range(1, 19))))
nflnews = get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?limit=60")

# ---------- schedule ----------
teams, games = {}, {}
for w in range(1, 19):
    games[w] = []
    for e in sbs[w].get("events", []):
        c = e["competitions"][0]
        comp = {t["homeAway"]: t for t in c["competitors"]}
        h, a = comp["home"], comp["away"]
        ha = ESPN_FIX.get(h["team"]["abbreviation"], h["team"]["abbreviation"])
        aa = ESPN_FIX.get(a["team"]["abbreviation"], a["team"]["abbreviation"])
        st = e["status"]["type"]
        odds = (c.get("odds") or [{}])[0]
        g = {"h": ha, "a": aa, "d": e["date"], "st": st.get("state"), "clock": st.get("shortDetail"),
             "hs": int(h.get("score") or 0), "as": int(a.get("score") or 0), "line": odds.get("details"), "ou": odds.get("overUnder")}
        games[w].append(g)
        for t, abbr, opp, home in ((h, ha, aa, True), (a, aa, ha, False)):
            tm = teams.setdefault(abbr, {"name": t["team"].get("displayName"), "short": t["team"].get("shortDisplayName"),
                                         "espn": t["team"]["id"], "sched": {}})
            rec = (t.get("records") or [{}])[0].get("summary")
            if rec and w <= CUR:
                tm["rec"] = rec
            tm["sched"][w] = {"o": opp, "h": home}
for abbr, tm in teams.items():
    tm["bye"] = next((w for w in range(1, 19) if w not in tm["sched"]), None)
espn_team = {int(t["espn"]): a for a, t in teams.items()}


def implied(w):
    out = {}
    for g in games.get(w, []):
        if g["ou"] is None or not g["line"] or g["line"] == "EVEN":
            continue
        try:
            fav, num = g["line"].split()
            num = abs(float(num))
            ou = float(g["ou"])
        except Exception:
            continue
        fav = ESPN_FIX.get(fav, fav)
        for t in (g["h"], g["a"]):
            out[t] = round(ou / 2 + (num / 2 if t == fav else -num / 2), 1)
    return out


imp = {CUR: implied(CUR), NEXT: implied(NEXT)}

# ---------- ESPN player feed ----------
def espn_rec(p):
    o = p.get("ownership", {})
    rec = {"own": round(o.get("percentOwned", 0), 1), "chg": round(o.get("percentChange", 0), 1),
           "start": round(o.get("percentStarted", 0), 1), "adp": round(o.get("averageDraftPosition", 0), 1)}
    for s in p.get("stats", []):
        if s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1:
            rc = s.get("stats", {}).get("53", 0) or 0
            tot = s.get("appliedTotal", 0)
            rec[f"p{s['scoringPeriodId']}"] = [round(tot, 2), round(tot - .5 * rc, 2), round(tot - rc, 2)]
    return rec


def norm(n):
    n = re.sub(r"[^a-z ]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
by_name = {}
for pid, p in players.items():
    if p.get("position") in POS and p.get("active"):
        by_name.setdefault((norm(p.get("full_name")), p.get("position")), []).append(pid)
sl_by_espn = {}
for pid, p in players.items():
    if p.get("espn_id") and p.get("position") in POS:
        try:
            sl_by_espn[int(p["espn_id"])] = pid
        except ValueError:
            pass


def map_espn(eid, name, posid, proteam):
    if eid in sl_by_espn:
        return sl_by_espn[eid]
    if posid == 16 or (eid is not None and eid < 0):
        return espn_team.get(proteam)
    c = by_name.get((norm(name), ESPN_POS.get(posid)), [])
    if len(c) > 1:
        tm = espn_team.get(proteam)
        c = [i for i in c if players[i].get("team") == tm] or c[:1]
    if c:
        sl_by_espn[eid] = c[0]
        return c[0]
    return None


es = {}
for x in espn:
    q = x["player"]
    sid = map_espn(q["id"], q.get("fullName"), q["defaultPositionId"], q.get("proTeamId"))
    if sid:
        es[sid] = espn_rec(q)
espn_of = {}
for eid, sid in sl_by_espn.items():
    espn_of.setdefault(sid, eid)


# ---------- leagues ----------
SLOT = {0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE", 7: "OP", 16: "DEF", 17: "K", 20: "BN", 21: "IR", 23: "FLEX", 24: "ER"}
leagues = {}
league_player_ids = set()


def fetch_league(lid):
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{lid}"
           "?view=mTeam&view=mRoster&view=mMatchupScore&view=mSettings&view=mStatus")
    try:
        return lid, get(url, tries=2)
    except urllib.error.HTTPError as e:
        return lid, {"__error": "private" if e.code in (401, 403) else "notfound"}
    except Exception:
        return lid, {"__error": "unavailable"}


for lid, L in pmap(fetch_league, LEAGUE_IDS, 4):
    if "__error" in L:
        leagues[lid] = {"id": lid, "error": L["__error"]}
        continue
    s = L.get("settings", {})
    period = (L.get("status") or {}).get("currentMatchupPeriod") or CUR
    sp = L.get("scoringPeriodId") or CUR
    items = {i.get("statId"): i.get("points") for i in (s.get("scoringSettings") or {}).get("scoringItems", [])}
    ppr = items.get(53, 0) or 0
    fmt = 0 if ppr >= 1 else 1 if ppr >= .5 else 2
    counts = {SLOT.get(int(k), k): v for k, v in ((s.get("rosterSettings") or {}).get("lineupSlotCounts") or {}).items() if v}
    tlist = []
    for t in L.get("teams", []):
        roster = []
        for en in (t.get("roster") or {}).get("entries", []):
            pe = (en.get("playerPoolEntry") or {}).get("player") or {}
            eid = en.get("playerId")
            sid = map_espn(eid, pe.get("fullName"), pe.get("defaultPositionId"), pe.get("proTeamId"))
            pj = act = None
            for st in pe.get("stats", []):
                if st.get("scoringPeriodId") == sp and st.get("statSplitTypeId") == 1:
                    if st.get("statSourceId") == 1:
                        pj = round(st.get("appliedTotal", 0), 2)
                    elif st.get("statSourceId") == 0:
                        act = round(st.get("appliedTotal", 0), 2)
            if sid:
                league_player_ids.add(sid)
            roster.append({"e": eid, "s": sid, "n": pe.get("fullName"), "pos": ESPN_POS.get(pe.get("defaultPositionId"), "?"),
                           "slot": SLOT.get(en.get("lineupSlotId"), str(en.get("lineupSlotId"))), "lp": pj, "la": act,
                           "inj": pe.get("injuryStatus") if pe.get("injuryStatus") not in (None, "ACTIVE") else None})
        rec = ((t.get("record") or {}).get("overall") or {})
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {t.get('id')}"
        tlist.append({"id": t.get("id"), "name": name, "abbr": t.get("abbrev"), "w": rec.get("wins", 0), "l": rec.get("losses", 0),
                      "t": rec.get("ties", 0), "pf": round(rec.get("pointsFor", 0), 1), "pa": round(rec.get("pointsAgainst", 0), 1),
                      "seed": t.get("playoffSeed"), "roster": roster})
    sched = []
    for m in L.get("schedule", []):
        mp = m.get("matchupPeriodId")
        if mp is None:
            continue
        h, a = m.get("home") or {}, m.get("away") or {}
        sched.append({"w": mp, "h": h.get("teamId"), "a": a.get("teamId"), "hp": round(h.get("totalPoints", 0) or 0, 1),
                      "ap": round(a.get("totalPoints", 0) or 0, 1), "win": m.get("winner")})
    leagues[lid] = {"id": lid, "name": s.get("name") or f"League {lid}", "fmt": fmt, "slots": counts, "period": period,
                    "sp": sp, "teams": tlist, "sched": sched}
print("leagues", {k: v.get("error", "ok") for k, v in leagues.items()})

# ---------- Sleeper projections / stats ----------
def by_id(rows):
    return {r["player_id"]: r for r in rows if r.get("stats")}


pw = {w: by_id(v) for w, v in proj_raw.items()}
pseason, sseason = by_id(pseason_raw), by_id(sseason_raw)
weekly = {w: by_id(v) for w, v in weekly_raw.items()}

KEYS = {"pass_att": "pa", "pass_cmp": "pc", "pass_yd": "py", "pass_td": "ptd", "pass_int": "int", "rush_att": "ra",
        "rush_yd": "ry", "rush_td": "rtd", "rec_tgt": "tgt", "rec": "rec", "rec_yd": "rcy", "rec_td": "rctd",
        "fum_lost": "fl", "fgm": "fgm", "fga": "fga", "xpm": "xpm", "def_sack": "sk", "def_int": "dint",
        "def_fum_rec": "fr", "def_td": "dtd", "pts_allow": "pa_"}


def slim(s, nd=1):
    return {v: round(s[k], nd) for k, v in KEYS.items() if s.get(k)}


def pts(s):
    return [round(s.get("pts_ppr", 0) or 0, 2), round(s.get("pts_half_ppr", 0) or 0, 2), round(s.get("pts_std", 0) or 0, 2)]


ids = set(league_player_ids)
for m, th in ((pw.get(CUR, {}), 2), (pw.get(NEXT, {}), 2), (pseason, 15), (sseason, 15)):
    for pid, r in m.items():
        if (r.get("player") or {}).get("position") in POS and (r["stats"].get("pts_ppr") or 0) > th:
            ids.add(pid)
for sid, e in es.items():
    if e["own"] > 3 or players.get(sid, {}).get("position") == "DEF":
        ids.add(sid)

out_players = []
for pid in ids:
    p = players.get(pid)
    if not p or p.get("position") not in POS or not p.get("team"):
        continue
    tm = p["team"]
    e = es.get(pid, {})
    rec = {"id": pid, "pos": p["position"], "tm": tm,
           "n": f"{teams.get(tm, {}).get('name', tm)} D/ST" if p["position"] == "DEF" else (p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}").strip()}
    if pid in espn_of:
        rec["eid"] = espn_of[pid]
    for k, kk in (("age", "age"), ("years_exp", "exp"), ("number", "num"), ("college", "col"), ("height", "ht"), ("weight", "wt"),
                  ("injury_status", "inj"), ("injury_body_part", "injb"), ("injury_notes", "injn"), ("practice_participation", "prac")):
        if p.get(k) not in (None, ""):
            rec[kk] = p[k]
    if e:
        rec["es"] = {k: v for k, v in e.items() if not k.startswith("p")}
    pr = {}
    for w in sorted({CUR, NEXT}):
        r = pw.get(w, {}).get(pid)
        d = {}
        if r:
            d["sl"] = pts(r["stats"])
            d["st"] = slim(r["stats"])
        if e.get(f"p{w}"):
            d["es"] = e[f"p{w}"]
        if d:
            pr[w] = d
    rec["pr"] = pr
    if pid in pseason:
        rec["seasonProj"] = pts(pseason[pid]["stats"])
    gl = []
    for w in range(1, CUR + 1):
        r = weekly.get(w, {}).get(pid)
        if r and (r["stats"].get("gp") or r["stats"].get("pts_ppr")):
            s = r["stats"]
            row = {"w": w, "o": r.get("opponent"), "p": pts(s), "s": slim(s, 0)}
            if s.get("off_snp") and s.get("tm_off_snp"):
                row["snap"] = round(100 * s["off_snp"] / s["tm_off_snp"])
            gl.append(row)
    rec["gl"] = gl
    if pid in sseason:
        rec["tot"] = pts(sseason[pid]["stats"])
        rec["ts"] = slim(sseason[pid]["stats"], 0)
    if not pr and not gl and pid not in league_player_ids:
        continue
    out_players.append(rec)

# ---------- fantasy points allowed ----------
fpa = {t: {pos: [] for pos in POS} for t in teams}
done_games = {(w, g["h"]) for w in games for g in games[w] if g["st"] == "post"} | {(w, g["a"]) for w in games for g in games[w] if g["st"] == "post"}
for w in range(1, CUR + 1):
    agg = {}
    for pid, r in weekly.get(w, {}).items():
        pos = (r.get("player") or {}).get("position")
        opp = r.get("opponent")
        if pos not in POS or opp not in teams or (w, opp) not in done_games:
            continue
        a = agg.setdefault((opp, pos), [0, 0, 0])
        for i, v in enumerate(pts(r["stats"])):
            a[i] += v
    for (opp, pos), v in agg.items():
        fpa[opp][pos].append(v)
fpa_out = {t: {pos: [round(sum(a[i] for a in arr) / len(arr), 2) for i in range(3)] for pos, arr in d.items() if arr} for t, d in fpa.items()}

# ---------- player news (ESPN, Rotowire-sourced headlines only) ----------
news_targets = [p for p in out_players if p.get("eid") and p["pos"] != "DEF" and (p.get("es", {}).get("own", 0) >= 25 or p["id"] in league_player_ids)]
cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)


def pnews(p):
    try:
        d = get(f"https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players?playerId={p['eid']}&limit=6", tries=2)
    except Exception:
        return []
    out = []
    for a in d.get("feed", []):
        t = a.get("published") or a.get("lastModified")
        try:
            if datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) < cut:
                continue
        except Exception:
            continue
        if a.get("type") != "Rotowire":
            continue  # multi-player stories live in the headlines feed
        kind = "note"
        href = ((a.get("links") or {}).get("web") or {}).get("href")
        out.append({"p": p["id"], "h": a.get("headline"), "t": t, "k": kind, "u": href})
        if len(out) >= 3:
            break
    return out


pn = [x for lst in pmap(pnews, news_targets, 12) for x in lst if x["h"]]
seen, player_news = set(), []
for x in sorted(pn, key=lambda x: x["t"], reverse=True):
    key = (x["p"], x["h"])
    if key in seen:
        continue
    seen.add(key)
    player_news.append(x)
headlines = []
for a in nflnews.get("articles", [])[:40]:
    href = ((a.get("links") or {}).get("web") or {}).get("href")
    if a.get("headline") and href:
        headlines.append({"h": a["headline"], "d": a.get("description"), "u": href, "t": a.get("published")})

out = {"season": SEASON, "week": CUR, "next": NEXT, "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "teams": teams, "games": {w: games[w] for w in range(CUR, 19)}, "implied": imp, "fpa": fpa_out, "players": out_players,
       "news": headlines, "pnews": player_news[:400], "trending": {"add": trend_add, "drop": trend_drop}, "leagues": leagues}
tmp = OUT + ".tmp"
with open(tmp, "w") as f:
    json.dump(out, f, separators=(",", ":"))
os.replace(tmp, OUT)
print(len(out_players), "players", len(player_news), "news", os.path.getsize(OUT) // 1024, "KB")
