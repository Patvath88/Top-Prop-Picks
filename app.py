import streamlit as st
import requests
import pandas as pd
from datetime import datetime
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
    if not search_term:
        return []
    data = api_get("players", {"search": search_term, "per_page": 50})
    unique = {}
    for p in data.get("data", []):
        key = (p["first_name"], p["last_name"])
        if key not in unique:
            unique[key] = p
    return list(unique.values())


def get_player_stats(player_id, seasons=None, game_ids=None):
    all_stats = []
    cursor = None

    while True:
        params = {"player_ids[]": player_id, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        if seasons:
            for s in seasons:
                params.setdefault("seasons[]", []).append(s)
        if game_ids:
            for gid in game_ids:
                params.setdefault("game_ids[]", []).append(gid)

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
# DEFENSIVE RATING (ALWAYS APPLIED)
# ---------------------------------------------------------

def get_team_def_rating(team_abbr, season_year):
    """Find team ID → compute defensive rating (points allowed per game)."""

    # Convert abbr → team_id using balldontlie team endpoint
    teams_data = api_get("teams")
    team_list = teams_data.get("data", [])

    team_id = None
    for t in team_list:
        if t["abbreviation"].upper() == team_abbr.upper():
            team_id = t["id"]
            break

    if not team_id:
        return 0.0

    # Pull all games for this team to compute defensive average
    all_games = []
    cursor = None
    while True:
        params = {"seasons[]": season_year, "per_page": 100}
        if cursor:
            params["cursor"] = cursor

        games = api_get("games", params)
        g = games.get("data", [])
        if not g:
            break

        for gm in g:
            if gm["home_team_id"] == team_id or gm["visitor_team_id"] == team_id:
                all_games.append(gm)

        cursor = games.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    finals = [gm for gm in all_games if gm.get("status") == "Final"]
    if not finals:
        return 0.0

    total_allowed = 0
    count = 0
    for gm in finals:
        if gm["home_team_id"] == team_id:
            total_allowed += gm["visitor_team_score"]
        else:
            total_allowed += gm["home_team_score"]
        count += 1

    return total_allowed / count if count else 0


# ---------------------------------------------------------
# DATA PROCESSING
# ---------------------------------------------------------

def _convert_minutes(val):
    if not val:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if ":" in val:
        m, s = val.split(":")
        try:
            return float(m) + float(s)/60
        except:
            return 0.0
    try:
        return float(val)
    except:
        return 0.0


def stats_list_to_df(stats):
    rows = []
    for s in stats:
        g = s.get("game", {})
        t = s.get("team", {})
        rows.append({
            "game_id": g.get("id"),
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
# HIT-RATE CALCULATION + CARD COLORS
# ---------------------------------------------------------

def compute_hit_rate(series, line):
    s = series.dropna()
    if s.empty:
        return 0, 0, 0
    hits = (s >= line).sum()
    total = len(s)
    return hits / total, hits, total


def glow_color(pct):
    if pct <= 0.50: return "#e74c3c"  # red
    if pct <= 0.60: return "#e67e22"  # orange
    if pct <= 0.70: return "#f1c40f"  # yellow
    return "#2ecc71"  # green


def render_metric_card(label, hits, total, avg):
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
