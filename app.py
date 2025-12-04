import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
import os

# ---------------------------------------------------------
# SETUP
# ---------------------------------------------------------

API_KEY = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"
BASE_URL = "https://api.balldontlie.io/v1"
HEADERS = {"Authorization": API_KEY}

# Streamlit Cloud writable directory
DATA_DIR = Path("/mount/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
SAVED_PROPS = DATA_DIR / "saved_props.csv"

NBA_TEAMS = [
    ("Atlanta Hawks", "ATL"), ("Boston Celtics", "BOS"), ("Brooklyn Nets", "BKN"),
    ("Charlotte Hornets", "CHA"), ("Chicago Bulls", "CHI"), ("Cleveland Cavaliers", "CLE"),
    ("Dallas Mavericks", "DAL"), ("Denver Nuggets", "DEN"), ("Detroit Pistons", "DET"),
    ("Golden State Warriors", "GSW"), ("Houston Rockets", "HOU"), ("Indiana Pacers", "IND"),
    ("Los Angeles Clippers", "LAC"), ("Los Angeles Lakers", "LAL"), ("Memphis Grizzlies", "MEM"),
    ("Miami Heat", "MIA"), ("Milwaukee Bucks", "MIL"), ("Minnesota Timberwolves", "MIN"),
    ("New Orleans Pelicans", "NOP"), ("New York Knicks", "NYK"), ("Oklahoma City Thunder", "OKC"),
    ("Orlando Magic", "ORL"), ("Philadelphia 76ers", "PHI"), ("Phoenix Suns", "PHX"),
    ("Portland Trail Blazers", "POR"), ("Sacramento Kings", "SAC"),
    ("San Antonio Spurs", "SAS"), ("Toronto Raptors", "TOR"),
    ("Utah Jazz", "UTA"), ("Washington Wizards", "WAS"),
]

STAT_MAP = {
    "Points": "pts", "Rebounds": "reb", "Assists": "ast",
    "Threes Made": "fg3m", "Steals": "stl", "Blocks": "blk",
    "Turnovers": "turnover", "Minutes": "min",
}

# ---------------------------------------------------------
# SEASON LOGIC (automatic & correct every year)
# ---------------------------------------------------------

def get_current_nba_season():
    """
    Returns the NBA season year for balldontlie.
    Example: 2025–2026 → returns 2025
    """
    now = datetime.now()
    year = now.year
    month = now.month

    # NBA season begins in October (10)
    if month >= 10:
        return year
    else:
        return year - 1


# ---------------------------------------------------------
# API WRAPPERS
# ---------------------------------------------------------

def api_get(endpoint, params=None):
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except:
        return {"data": []}

def get_players(query):
    raw = api_get("players", {"search": query, "per_page": 50})
    uniq = {}
    for p in raw.get("data", []):
        uniq.setdefault((p["first_name"], p["last_name"]), p)
    return list(uniq.values())

def get_stats(player_id, season):
    results = []
    cursor = None
    while True:
        params = {"player_ids[]": player_id, "seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        res = api_get("stats", params)
        batch = res.get("data", [])
        if not batch:
            break
        results.extend(batch)
        cursor = res.get("meta", {}).get("next_cursor")
        if not cursor:
            break
    return results


# ---------------------------------------------------------
# DEFENSIVE RATING (correct implementation)
# ---------------------------------------------------------

def get_def_rating(team_abbr, season):
    teams = api_get("teams").get("data", [])
    team_id = next((t["id"] for t in teams if t["abbreviation"] == team_abbr), None)
    if not team_id:
        return 0.0

    all_games = []
    cursor = None

    while True:
        params = {"seasons[]": season, "per_page": 100, "season_type": "regular"}
        if cursor:
            params["cursor"] = cursor

        res = api_get("games", params)
        games = res.get("data", [])
        if not games:
            break

        for gm in games:
            if gm.get("status") != "Final":
                continue
            if gm.get("home_team_id") is None or gm.get("visitor_team_id") is None:
                continue
            if gm.get("home_team_score") is None or gm.get("visitor_team_score") is None:
                continue

            if gm["home_team_id"] == team_id or gm["visitor_team_id"] == team_id:
                all_games.append(gm)

        cursor = res.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    if not all_games:
        return 0.0

    allowed = []
    for gm in all_games:
        if gm["home_team_id"] == team_id:
            allowed.append(gm["visitor_team_score"])
        else:
            allowed.append(gm["home_team_score"])

    return sum(allowed) / len(allowed)


# ---------------------------------------------------------
# DATA PROCESSING
# ---------------------------------------------------------

def convert_minutes(v):
    if not v:
        return 0
    if isinstance(v, (int, float)):
        return float(v)
    if ":" in str(v):
        m, s = v.split(":")
        return float(m) + float(s) / 60
    try:
        return float(v)
    except:
        return 0

def stats_to_df(stats):
    rows = []
    for s in stats:
        g = s["game"]
        t = s["team"]
        rows.append({
            "date": pd.to_datetime(g["date"][:10]),
            "team_id": t["id"],
            "home_id": g.get("home_team_id"),
            "away_id": g.get("visitor_team_id"),
            "pts": s.get("pts"),
            "reb": s.get("reb"),
            "ast": s.get("ast"),
            "fg3m": s.get("fg3m"),
            "stl": s.get("stl"),
            "blk": s.get("blk"),
            "turnover": s.get("turnover"),
            "min": convert_minutes(s.get("min")),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------
# METRICS
# ---------------------------------------------------------

def hit_rate(series, line):
    s = series.dropna()
    if len(s) == 0:
        return 0, 0, 0
    hits = (s >= line).sum()
    return hits / len(s), hits, len(s)

def glow_color(p):
    if p <= 0.50: return "#e74c3c"
    if p <= 0.60: return "#e67e22"
    if p <= 0.70: return "#f1c40f"
    return "#2ecc71"

def card(label, hits, total, avg):
    pct = hits / total if total > 0 else 0
    pct_txt = f"{pct*100:.0f}%"
    hit_txt = f"{hits}/{total}"
    c = glow_color(pct)

    st.markdown(
        f"""
        <div style="
            background:#111;
            border-radius:10px;
            padding:14px;
            border:2px solid {c};
            box-shadow:0 0 12px {c};
            color:white;
        ">
            <div style="font-size:13px;color:#ccc;">{label}</div>
            <div style="font-size:22px;font-weight:700;">{hit_txt} ({pct_txt})</div>
            <div style="font-size:14px;color:#bbb;">Avg: {avg:.1f}</div>
        </div>
        """,
        unsafe_allow_html=True
    )


# ---------------------------------------------------------
# PROJECTION MODEL
# ---------------------------------------------------------

def projected_value(last10, season_avg, home_avg, def_rating):
    base = 0.5*last10 + 0.3*season_avg + 0.2*home_avg
    league_avg = 114
    adj = 1 + ((league_avg - def_rating) / league_avg) * 0.4 if def_rating else 1
    return base * adj


# ---------------------------------------------------------
# CSV SAVE & LOAD
# ---------------------------------------------------------

def load_saved():
    if not SAVED_PROPS.exists():
        return pd.DataFrame()
    return pd.read_csv(SAVED_PROPS)

def save_prop(row):
    df = load_saved()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(SAVED_PROPS, index=False)


# ---------------------------------------------------------
# ANALYSIS ENGINE
# ---------------------------------------------------------

def analyze(player, stat, line, odds, opp_abbr):
    season = get_current_nba_season()

    stats = get_stats(player["id"], season)
    df = stats_to_df(stats)

    field = STAT_MAP[stat]

    # Last 10
    last10 = df.tail(10)[field]
    last10_avg = last10.mean()
    r10, h10, t10 = hit_rate(last10, line)

    # Season
    season_avg = df[field].mean()
    rs, hs, ts2 = hit_rate(df[field], line)

    # Home / Away
    home = df[df["team_id"] == df["home_id"]][field]
    away = df[df["team_id"] != df["home_id"]][field]

    home_avg = home.mean()
    rh, hh, th = hit_rate(home, line)

    away_avg = away.mean()
    ra, ha, ta = hit_rate(away, line)

    # H2H
    teams = api_get("teams").get("data", [])
    opp_id = next((t["id"] for t in teams if t["abbreviation"] == opp_abbr), None)

    if opp_id:
        vs_df = df[(df["home_id"] == opp_id) | (df["away_id"] == opp_id)]
        vs_avg = vs_df[field].mean() if not vs_df.empty else 0
        rv, hv, tv = hit_rate(vs_df[field], line) if not vs_df.empty else (0,0,0)
    else:
        vs_avg = 0
        rv, hv, tv = 0,0,0

    # Defensive Rating
    def_rating = get_def_rating(opp_abbr, season)

    # Projection
    proj = projected_value(last10_avg, season_avg, home_avg, def_rating)

    # ---- Display ----

    st.markdown("### Performance Metrics")

    c1, c2, c3 = st.columns(3)
    with c1: card("Last 10 ≥ Line", h10, t10, last10_avg)
    with c2: card("Season ≥ Line", hs, ts2, season_avg)
    with c3: card("Home ≥ Line", hh, th, home_avg)

    c4, c5, c6 = st.columns(3)
    with c4: card("Away ≥ Line", ha, ta, away_avg)
    with c5: card("Vs Opponent ≥ Line", hv, tv, vs_avg)
    with c6: card("Opponent Def Rating", 0, 0, def_rating)

    st.subheader("Projection")
    st.metric(f"Projected {stat}", f"{proj:.1f}")

    # Save Prop
    if st.button("Save This Prop"):
        save_prop({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "player": f"{player['first_name']} {player['last_name']}",
            "player_id": player["id"],
            "team": player["team"]["abbreviation"],
            "stat": stat,
            "line": line,
            "odds": odds,
            "projection": proj,
            "opponent": opp_abbr,
            "outcome": "Pending"
        })
        st.success("Saved!")


# ---------------------------------------------------------
# UI LAYOUT
# ---------------------------------------------------------

def main():
    st.set_page_config(page_title="Top Prop Picks", layout="wide")
    st.title("Top Prop Picks – NBA Prop Evaluator")

    query = st.text_input("Search Player:")
    player = None

    if query:
        players = get_players(query)
        if players:
            labels = [f"{p['first_name']} {p['last_name']} ({p['team']['abbreviation']})" for p in players]
            sel = st.selectbox("Select Player:", labels)
            player = players[labels.index(sel)]

    stat = st.selectbox("Stat Type:", list(STAT_MAP.keys()))
    line = st.number_input("Betting Line:", min_value=0.0, step=0.5)
    odds = st.text_input("Odds:")

    opp_list = [f"{name} ({abbr})" for name, abbr in NBA_TEAMS]
    opp_choice = st.selectbox("Opponent:", opp_list)
    opp_abbr = opp_choice.split("(")[-1].replace(")", "")

    if player and st.button("Run Analysis", type="primary"):
        analyze(player, stat, line, odds, opp_abbr)

    # Saved Props Table
    st.markdown("---")
    st.subheader("Saved Props")
    df = load_saved()
    if df.empty:
        st.info("No saved props yet.")
    else:
        st.dataframe(df.sort_values("timestamp", ascending=False), hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
