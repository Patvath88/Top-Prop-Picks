import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

BALLDONTLIE_API_KEY = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"
BASE_URL = "https://api.balldontlie.io/v1"
HEADERS = {"Authorization": BALLDONTLIE_API_KEY}

DATA_DIR = Path(".")
SAVED_PROPS_CSV = DATA_DIR / "saved_props.csv"

# Hardcoded NBA Teams (Full Name + Abbreviation)
NBA_TEAMS = [
    ("Atlanta Hawks", "ATL"),
    ("Boston Celtics", "BOS"),
    ("Brooklyn Nets", "BKN"),
    ("Charlotte Hornets", "CHA"),
    ("Chicago Bulls", "CHI"),
    ("Cleveland Cavaliers", "CLE"),
    ("Dallas Mavericks", "DAL"),
    ("Denver Nuggets", "DEN"),
    ("Detroit Pistons", "DET"),
    ("Golden State Warriors", "GSW"),
    ("Houston Rockets", "HOU"),
    ("Indiana Pacers", "IND"),
    ("Los Angeles Clippers", "LAC"),
    ("Los Angeles Lakers", "LAL"),
    ("Memphis Grizzlies", "MEM"),
    ("Miami Heat", "MIA"),
    ("Milwaukee Bucks", "MIL"),
    ("Minnesota Timberwolves", "MIN"),
    ("New Orleans Pelicans", "NOP"),
    ("New York Knicks", "NYK"),
    ("Oklahoma City Thunder", "OKC"),
    ("Orlando Magic", "ORL"),
    ("Philadelphia 76ers", "PHI"),
    ("Phoenix Suns", "PHX"),
    ("Portland Trail Blazers", "POR"),
    ("Sacramento Kings", "SAC"),
    ("San Antonio Spurs", "SAS"),
    ("Toronto Raptors", "TOR"),
    ("Utah Jazz", "UTA"),
    ("Washington Wizards", "WAS"),
]

# ---------------------------------------------------------
# FIELD MAP
# ---------------------------------------------------------

STAT_FIELD_MAP = {
    "Points": "pts",
    "Rebounds": "reb",
    "Assists": "ast",
    "Threes Made": "fg3m",
    "Steals": "stl",
    "Blocks": "blk",
    "Turnovers": "turnover",
    "Minutes": "min",
}

# ---------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------

def api_get(endpoint, params=None):
    if params is None:
        params = {}
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except:
        return {"data": []}


def get_active_players(search_term):
    data = api_get("players", {"search": search_term, "per_page": 50})
    unique = {}
    for p in data.get("data", []):
        unique.setdefault((p["first_name"], p["last_name"]), p)
    return list(unique.values())


def get_player_stats(player_id, seasons=None):
    all_stats = []
    cursor = None

    while True:
        params = {"player_ids[]": player_id, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        if seasons:
            for s in seasons:
                params.setdefault("seasons[]", []).append(s)

        res = api_get("stats", params)
        stats = res.get("data", [])
        if not stats:
            break

        all_stats.extend(stats)
        cursor = res.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    return all_stats

# ---------------------------------------------------------
# DEFENSIVE RATING (patched)
# ---------------------------------------------------------

def get_team_def_rating(team_abbr, season_year):
    """Computes average points allowed per game for a team (safe version)."""

    teams = api_get("teams").get("data", [])
    team_id = next((t["id"] for t in teams if t["abbreviation"].upper() == team_abbr.upper()), None)

    if not team_id:
        return 0.0

    all_games = []
    cursor = None

    while True:
        params = {"seasons[]": season_year, "per_page": 100}
        if cursor:
            params["cursor"] = cursor

        res = api_get("games", params)
        games = res.get("data", [])
        if not games:
            break

        for gm in games:
            home_id = gm.get("home_team_id")
            away_id = gm.get("visitor_team_id")

            # skip malformed objects
            if home_id is None or away_id is None:
                continue

            if home_id == team_id or away_id == team_id:
                all_games.append(gm)

        cursor = res.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    finals = [gm for gm in all_games if gm.get("status") == "Final"]
    if not finals:
        return 0.0

    allowed = []
    for gm in finals:
        home_id = gm.get("home_team_id")
        home_score = gm.get("home_team_score", 0)
        away_score = gm.get("visitor_team_score", 0)

        if home_id == team_id:
            allowed.append(away_score)
        else:
            allowed.append(home_score)

    return sum(allowed) / len(allowed) if allowed else 0.0

# ---------------------------------------------------------
# DATA PROCESSING
# ---------------------------------------------------------

def _convert_minutes(v):
    if not v:
        return 0
    if isinstance(v, (float, int)):
        return float(v)
    if ":" in v:
        m, s = v.split(":")
        return float(m) + float(s)/60
    try:
        return float(v)
    except:
        return 0


def stats_df(stats):
    rows = []
    for s in stats:
        g = s.get("game", {})
        t = s.get("team", {})
        rows.append({
            "date": pd.to_datetime(g.get("date", "")[:10], errors="coerce"),
            "home_team_id": g.get("home_team_id"),
            "visitor_team_id": g.get("visitor_team_id"),
            "team_id": t.get("id"),
            "pts": s.get("pts"),
            "reb": s.get("reb"),
            "ast": s.get("ast"),
            "fg3m": s.get("fg3m"),
            "stl": s.get("stl"),
            "blk": s.get("blk"),
            "turnover": s.get("turnover"),
            "min": _convert_minutes(s.get("min")),
        })
    return pd.DataFrame(rows)

# ---------------------------------------------------------
# HIT RATES + CARDS
# ---------------------------------------------------------

def hit_rate(series, line):
    s = series.dropna()
    if s.empty:
        return 0, 0, 0
    hits = (s >= line).sum()
    total = len(s)
    return hits/total, hits, total


def glow_color(p):
    if p <= 0.50: return "#e74c3c"
    if p <= 0.60: return "#e67e22"
    if p <= 0.70: return "#f1c40f"
    return "#2ecc71"


def metric_card(label, hits, total, avg):
    pct = hits/total if total > 0 else 0
    c = glow_color(pct)
    pct_txt = f"{pct*100:.0f}%"
    hit_txt = f"{hits}/{total}"

    st.markdown(
        f"""
        <div style="
            border:3px solid {c};
            box-shadow:0 0 12px {c};
            border-radius:12px;
            padding:12px;
            margin:6px;
            background:white;">
            <div style="font-size:13px;color:#555;">{label}</div>
            <div style="font-size:20px;font-weight:700;">{hit_txt} ({pct_txt})</div>
            <div style="font-size:14px;">Avg: {avg:.1f}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# ---------------------------------------------------------
# PROJECTION
# ---------------------------------------------------------

def projection(l10, season, home, def_rating):
    base = (0.5*l10 + 0.3*season + 0.2*home)
    league_avg = 114
    adj = 1 + ((league_avg - def_rating) / league_avg) * 0.5 if def_rating else 1
    return base * adj

# ---------------------------------------------------------
# SAVING
# ---------------------------------------------------------

def load_props():
    if not SAVED_PROPS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(SAVED_PROPS_CSV)


def save_props(df):
    df.to_csv(SAVED_PROPS_CSV, index=False)

# ---------------------------------------------------------
# ANALYSIS ENGINE
# ---------------------------------------------------------

def run_analysis(player, stat, line_val, odds_val, opponent_abbr):
    st.subheader(f"{player['first_name']} {player['last_name']} – {stat}")

    today = datetime.now(timezone.utc).date()
    season = today.year

    stats = get_player_stats(player["id"], seasons=[season])
    df = stats_df(stats)
    if df.empty:
        st.error("No stats found for this player.")
        return

    field = STAT_FIELD_MAP[stat]

    # Last 10
    last10 = df.tail(10)
    l10_avg = last10[field].mean()
    r10, h10, t10 = hit_rate(last10[field], line_val)

    # Season
    season_avg = df[field].mean()
    rs, hs, ts = hit_rate(df[field], line_val)

    # Home/Away
    home_series = df[df["team_id"] == df["home_team_id"]][field]
    away_series = df[df["team_id"] != df["home_team_id"]][field]

    home_avg = home_series.mean()
    rh, hh, th = hit_rate(home_series, line_val)

    away_avg = away_series.mean()
    ra, ha, ta = hit_rate(away_series, line_val)

    # Vs upcoming opponent (always shown)
    opp_teams = api_get("teams").get("data", [])
    opp_id = next((t["id"] for t in opp_teams if t["abbreviation"] == opponent_abbr), None)

    if opp_id:
        vs_df = df[(df["home_team_id"] == opp_id) | (df["visitor_team_id"] == opp_id)]
        vs_avg = vs_df[field].mean() if not vs_df.empty else 0
        rv, hv, tv = hit_rate(vs_df[field], line_val) if not vs_df.empty else (0, 0, 0)
    else:
        vs_avg = 0
        rv, hv, tv = 0, 0, 0

    # Defensive rating
    def_rating = get_team_def_rating(opponent_abbr, season)

    # Projection
    proj = projection(l10_avg, season_avg, home_avg, def_rating)

    # -----------------------------------------------------
    # Metric card grid
    # -----------------------------------------------------
    st.markdown("### Hit Rates & Context")

    c1, c2, c3 = st.columns(3)
    with c1: metric_card("Last 10 ≥ Line", h10, t10, l10_avg)
    with c2: metric_card("Season ≥ Line", hs, ts, season_avg)
    with c3: metric_card("Home ≥ Line", hh, th, home_avg)

    c4, c5, c6 = st.columns(3)
    with c4: metric_card("Away ≥ Line", ha, ta, away_avg)
    with c5: metric_card("Vs Opponent ≥ Line", hv, tv, vs_avg)
    with c6: metric_card("Opponent Def Rating", 0, 0, def_rating)

    st.subheader("Projected Outcome")
    st.metric(f"Projected {stat}", f"{proj:.1f}")

    # Save Prop
    if st.button("Save This Prop"):
        dfp = load_props()
        new = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "player": f"{player['first_name']} {player['last_name']}",
            "player_id": player["id"],
            "team": player["team"]["abbreviation"],
            "stat": stat,
            "line": line_val,
            "odds": odds_val,
            "projection": proj,
            "opponent": opponent_abbr,
            "outcome": "Pending",
        }
        dfp = pd.concat([dfp, pd.DataFrame([new])], ignore_index=True)
        save_props(dfp)
        st.success("Prop Saved!")

# ---------------------------------------------------------
# STREAMLIT UI
# ---------------------------------------------------------

def main():
    st.set_page_config(page_title="Top Prop Picks", layout="wide")

    st.title("Top Prop Picks – NBA Prop Evaluator")

    search = st.text_input("Search Player:")
    player = None

    if search:
        players = get_active_players(search)
        if players:
            names = [f"{p['first_name']} {p['last_name']} ({p['team']['abbreviation']})" for p in players]
            selected = st.selectbox("Select Player:", names)
            player = players[names.index(selected)]

    stat = st.selectbox("Stat:", list(STAT_FIELD_MAP.keys()))
    line_val = st.number_input("Line:", min_value=0.0, step=0.5)
    odds_val = st.text_input("Odds:")

    opp_options = [f"{name} ({abbr})" for name, abbr in NBA_TEAMS]
    opp_choice = st.selectbox("Upcoming Opponent:", opp_options)
    opponent_abbr = opp_choice.split("(")[-1].replace(")", "")

    if player and st.button("Run Analysis", type="primary"):
        run_analysis(player, stat, line_val, odds_val, opponent_abbr)

    st.markdown("---")
    st.subheader("Saved Props")

    dfp = load_props()
    if dfp.empty:
        st.info("No saved props yet.")
    else:
        st.dataframe(dfp.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
