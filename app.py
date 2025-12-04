import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path

# -----------------------------------------------
# CONFIG
# -----------------------------------------------

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

# -----------------------------------------------
# STAT FIELD MAPPINGS
# -----------------------------------------------

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

# -----------------------------------------------
# API HELPERS
# -----------------------------------------------

def api_get(endpoint: str, params=None):
    if params is None:
        params = {}
    url = f"{BASE_URL}/{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except:
        return {"data": []}


def get_active_players(search_term: str):
    if not search_term:
        return []
    data = api_get("players", {"search": search_term, "per_page": 50})
    unique = {}
    for p in data.get("data", []):
        key = (p["first_name"], p["last_name"])
        if key not in unique:
            unique[key] = p
    return list(unique.values())


def get_player_stats(player_id: int, seasons=None, game_ids=None):
    all_stats, cursor = [], None
    while True:
        params = {"player_ids[]": player_id, "per_page": 100}
        if cursor: params["cursor"] = cursor
        if seasons:
            for s in seasons:
                params.setdefault("seasons[]", []).append(s)
        if game_ids:
            for gid in game_ids:
                params.setdefault("game_ids[]", []).append(gid)

        data = api_get("stats", params)
        entries = data.get("data", [])
        if not entries:
            break
        all_stats.extend(entries)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor: break
    return all_stats


def get_team_defensive_rating(opponent_team_id, season, as_of):
    all_games, cursor = [], None
    while True:
        params = {"seasons[]": season, "end_date": as_of, "per_page": 100}
        if cursor: params["cursor"] = cursor
        data = api_get("games", params)
        g = data.get("data", [])
        if not g:
            break
        for gm in g:
            if gm["home_team_id"] == opponent_team_id or gm["visitor_team_id"] == opponent_team_id:
                all_games.append(gm)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    finals = [gm for gm in all_games if gm.get("status") == "Final"]
    if not finals:
        return 0.0

    total, count = 0, 0
    for gm in finals:
        if gm["home_team_id"] == opponent_team_id:
            total += gm["visitor_team_score"]
        else:
            total += gm["home_team_score"]
        count += 1

    return total / count if count else 0.0


# -----------------------------------------------
# DATA PROCESSING
# -----------------------------------------------

def _convert_minutes(v):
    if not v:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if ":" in v:
        m, s = v.split(":")
        try:
            return float(m) + float(s)/60
        except:
            return 0.0
    try:
        return float(v)
    except:
        return 0.0


def stats_list_to_df(stats):
    rows = []
    for s in stats:
        g = s.get("game", {})
        t = s.get("team", {})
        rows.append({
            "game_id": g.get("id"),
            "date": g.get("date"),
            "season": g.get("season"),
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
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"].str[:10], errors="coerce")
    return df


def compute_hit_rate(series, line):
    s = series.dropna()
    if s.empty: return 0, 0, 0
    hits = (s >= line).sum()
    total = len(s)
    return hits / total, hits, total


# -----------------------------------------------
# METRIC CARD RENDERING (GLOW COLORS)
# -----------------------------------------------

def glow_color(pct: float):
    if pct <= 0.50:
        return "#e74c3c"  # red
    elif pct <= 0.60:
        return "#e67e22"  # orange
    elif pct <= 0.70:
        return "#f1c40f"  # yellow
    else:
        return "#2ecc71"  # green


def render_metric_card(label: str, hits: int, total: int, avg: float):
    if total == 0:
        pct = 0.0
        pct_text = "0%"
        hits_text = "0/0"
    else:
        pct = hits / total
        pct_text = f"{pct*100:.0f}%"
        hits_text = f"{hits}/{total}"

    color = glow_color(pct)

    st.markdown(
        f"""
        <div style="
            border: 3px solid {color};
            box-shadow: 0 0 12px {color};
            border-radius: 12px;
            padding: 12px;
            margin: 6px;
            background-color: #ffffff;
        ">
            <div style="font-size:13px; color:#7f8c8d;">{label}</div>
            <div style="font-size:20px; font-weight:700; color:#2c3e50;">{hits_text} ({pct_text})</div>
            <div style="font-size:14px; color:#2c3e50;">Avg: {avg:.1f}</div>
        </div>
        """,
        unsafe_allow_html=True
    )


# -----------------------------------------------
# PROJECTION MODEL
# -----------------------------------------------

def simple_projection(last10, season, homeaway, defrtg):
    base = 0.5 * last10 + 0.3 * season + 0.2 * homeaway
    league_avg = 114
    if defrtg > 0:
        diff = league_avg - defrtg
        adj = 1 + (diff / league_avg) * 0.5
    else:
        adj = 1
    return base * adj


# -----------------------------------------------
# SAVE / LOAD
# -----------------------------------------------

def load_saved_props():
    if not SAVED_PROPS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(SAVED_PROPS_CSV)


def save_props(df):
    df.to_csv(SAVED_PROPS_CSV, index=False)


# -----------------------------------------------
# MAIN ANALYSIS
# -----------------------------------------------

def run_analysis(player, stat, line_value, odds_value, opponent_abbr):
    st.subheader(f"{player['first_name']} {player['last_name']} – {stat}")

    today = datetime.utcnow().date()
    season = today.year

    stats = get_player_stats(player["id"], seasons=[season])
    df = stats_list_to_df(stats)
    if df.empty:
        st.error("No game logs found.")
        return

    df = df.sort_values("date")
    field = STAT_FIELD_MAP[stat]

    # Last 10
    last10 = df.tail(10)
    last10_avg = last10[field].mean()
    r10, h10, t10 = compute_hit_rate(last10[field], line_value)

    # Season
    season_avg = df[field].mean()
    rs, hs, ts = compute_hit_rate(df[field], line_value)

    # Home/Away
    home_mask = df["team_id"] == df["home_team_id"]
    away_mask = ~home_mask

    home_avg = df[field][home_mask].mean()
    ah, hh, th = compute_hit_rate(df[field][home_mask], line_value)

    away_avg = df[field][away_mask].mean()
    ra, ha, ta = compute_hit_rate(df[field][away_mask], line_value)

    # Vs Opponent
    opponent_team_id = None
    for name, ab in NBA_TEAMS:
        if ab == opponent_abbr:
            opponent_team_id = None  # BDL IDs unavailable; historical vs opp disabled
            break

    vs_avg = 0.0
    rv = hv = tv = 0

    # Opponent defensive rating
    opp_def = 0.0

    # Projection
    projection = simple_projection(last10_avg, season_avg, home_avg, opp_def)

    # -----------------------------------
    # METRIC CARDS (3 per row)
    # -----------------------------------

    st.markdown("### Hit Rates")

    c1, c2, c3 = st.columns(3)
    with c1:
        render_metric_card("Last 10 ≥ Line", h10, t10, last10_avg)
    with c2:
        render_metric_card("Season ≥ Line", hs, ts, season_avg)
    with c3:
        render_metric_card("Home ≥ Line", hh, th, home_avg)

    c4, c5, c6 = st.columns(3)
    with c4:
        render_metric_card("Away ≥ Line", ha, ta, away_avg)
    with c5:
        render_metric_card("Vs Opponent ≥ Line", hv, tv, vs_avg)
    with c6:
        render_metric_card("Opponent Defensive Rating", 0, 0, opp_def)

    st.subheader("Projection")
    st.metric(f"Projected {stat}", f"{projection:.1f}")

    # Saving logic
   if st.button("Save Prop"):
    dfp = load_saved_props()

    new_row = {
        "timestamp": datetime.utcnow().isoformat(),
        "player_id": player["id"],
        "player_name": f"{player['first_name']} {player['last_name']}",
        "team_abbr": player["team"]["abbreviation"],
        "stat": stat,
        "line": line_value,
        "odds": odds_value,
        "projection": projection,
        "opponent": opponent_abbr,
        "outcome": "Pending",
    }

    # Ensure all columns exist
    for col in new_row.keys():
        if col not in dfp.columns:
            dfp[col] = ""

    dfp = pd.concat([dfp, pd.DataFrame([new_row])], ignore_index=True)

    save_props(dfp)

    st.success("Prop Saved Successfully!")

# -----------------------------------------------
# STREAMLIT UI
# -----------------------------------------------

def main():
    st.set_page_config(page_title="Top Prop Picks", layout="wide")
    st.title("Top Prop Picks – NBA Prop Evaluator")

    search_term = st.text_input("Search Player:")

    player = None
    if search_term:
        players = get_active_players(search_term)
        if players:
            names = [
                f"{p['first_name']} {p['last_name']} ({p['team']['abbreviation']})"
                for p in players
            ]
            choice = st.selectbox("Select Player:", names)
            player = players[names.index(choice)]

    stat = st.selectbox("Stat:", list(STAT_FIELD_MAP.keys()))
    line_value = st.number_input("Line:", min_value=0.0, step=0.5)
    odds_value = st.text_input("Odds:")

    opponent_options = [f"{name} ({abbr})" for name, abbr in NBA_TEAMS]
    selected_opp = st.selectbox("Upcoming Opponent:", opponent_options)
    opponent_abbr = selected_opp.split("(")[-1].replace(")", "")

    if player and st.button("Run Analysis"):
        run_analysis(player, stat, line_value, odds_value, opponent_abbr)

    st.markdown("---")
    st.subheader("Saved Props")
    dfp = load_saved_props()
    if not dfp.empty:
        st.dataframe(dfp)


if __name__ == "__main__":
    main()
