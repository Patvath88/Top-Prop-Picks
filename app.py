import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# -----------------------------------------------------------------------------
# Top Prop Picks Streamlit App
# -----------------------------------------------------------------------------

BALLDONTLIE_API_KEY = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"
BASE_URL = "https://api.balldontlie.io/v1"
HEADERS = {"Authorization": BALLDONTLIE_API_KEY}

DATA_DIR = Path(".")
SAVED_PROPS_CSV = DATA_DIR / "saved_props.csv"

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

# -----------------------------------------------------------------------------
# BALDONTLIE HELPERS
# -----------------------------------------------------------------------------
def api_get(endpoint: str, params: dict | None = None) -> dict:
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


def get_player_stats(player_id: int, start_date=None, end_date=None, seasons=None, game_ids=None):
    all_stats = []
    cursor = None
    while True:
        params = {"player_ids[]": player_id, "per_page": 100}
        if cursor: params["cursor"] = cursor
        if start_date: params["start_date"] = start_date
        if end_date: params["end_date"] = end_date
        if seasons:
            for s in seasons:
                params.setdefault("seasons[]", []).append(s)
        if game_ids:
            for gid in game_ids:
                params.setdefault("game_ids[]", []).append(gid)

        data = api_get("stats", params)
        stats = data.get("data", [])
        if not stats:
            break
        all_stats.extend(stats)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor: break
    return all_stats


# -----------------------------------------------------------------------------
# *** REPLACEMENT FUNCTION ***
# Uses FREE NBA API: data.nba.net
# -----------------------------------------------------------------------------
def get_upcoming_game(player: dict):
    """
    Pull upcoming schedule from https://data.nba.net/
    ALWAYS returns the next game for the player's team.
    """
    abbr = player.get("team", {}).get("abbreviation")
    if not abbr:
        return None

    today = datetime.utcnow().date()
    year = today.year

    # NBA API uses lowercase team abbreviations
    abbr = abbr.lower()

    url = f"https://data.nba.net/prod/v2/{year}/teams/{abbr}/schedule.json"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except:
        return None

    games = data.get("league", {}).get("standard", [])
    next_game = None

    for g in games:
        date_str = g.get("startDateEastern")  # example: "20250118"
        if not date_str:
            continue

        try:
            game_date = datetime.strptime(date_str, "%Y%m%d").date()
        except:
            continue

        if game_date >= today:  # future or today
            if next_game is None or game_date < next_game["parsed_date"]:
                is_home = g.get("isHomeTeam", False)
                opponent = g["vTeam"]["teamId"] if is_home else g["hTeam"]["teamId"]

                next_game = {
                    "gameId": g.get("gameId"),
                    "parsed_date": game_date,
                    "date_str": date_str,
                    "is_home": is_home,
                    "opponent_team_id": opponent,
                }

    return next_game


# -----------------------------------------------------------------------------
# Defensive Rating Approx (same as before)
# -----------------------------------------------------------------------------
def get_team_defensive_rating(team_id, season, as_of):
    all_games, cursor = [], None
    while True:
        params = {"seasons[]": season, "per_page": 100, "end_date": as_of}
        if cursor: params["cursor"] = cursor

        data = api_get("games", params)
        games = data.get("data", [])
        if not games: break

        for g in games:
            if g.get("home_team_id") == team_id or g.get("visitor_team_id") == team_id:
                all_games.append(g)

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor: break

    final_games = [g for g in all_games if g.get("status") == "Final"]
    if not final_games:
        return 0.0

    total = 0
    count = 0
    for g in final_games:
        if g["home_team_id"] == team_id:
            total += g["visitor_team_score"]
        else:
            total += g["home_team_score"]
        count += 1

    return total / count if count else 0


# -----------------------------------------------------------------------------
# Data Processing (unchanged)
# -----------------------------------------------------------------------------
def stats_list_to_df(stats):
    if not stats:
        return pd.DataFrame()

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
            "home_team_score": g.get("home_team_score"),
            "visitor_team_score": g.get("visitor_team_score"),
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


def compute_hit_rate(series, line):
    s = series.dropna()
    if s.empty: return 0, 0, 0
    hits = (s >= line).sum()
    total = len(s)
    return hits / total, hits, total


def format_percentage_card(hits, total):
    if total == 0:
        return "0/0 (0%)", "red"
    pct = hits / total
    pct_str = f"{pct*100:.0f}%"
    color = "green" if pct > 0.7 else "gold" if pct >= 0.5 else "red"
    return f"{hits}/{total} ({pct_str})", color


# -----------------------------------------------------------------------------
# Projection Model (unchanged)
# -----------------------------------------------------------------------------
def simple_projection(last10, season, homeaway, defrtg):
    base = 0.5 * last10 + 0.3 * season + 0.2 * homeaway
    league_avg = 114
    if defrtg > 0:
        diff = league_avg - defrtg
        adj = 1 + (diff / league_avg) * 0.5
    else:
        adj = 1
    return base * adj


# -----------------------------------------------------------------------------
# Saving / Updating Props (unchanged)
# -----------------------------------------------------------------------------
def load_saved_props():
    if not SAVED_PROPS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(SAVED_PROPS_CSV)


def save_props(df):
    df.to_csv(SAVED_PROPS_CSV, index=False)


def update_outcomes(df):
    pending = df[df["outcome"] == "Pending"]
    if pending.empty:
        return df

    for idx, row in pending.iterrows():
        stats = get_player_stats(int(row["player_id"]), game_ids=[row["game_id"]])
        if not stats:
            continue
        df_stats = stats_list_to_df(stats)
        stat_field = STAT_FIELD_MAP[row["stat"]]
        val = df_stats.iloc[0][stat_field]
        df.loc[idx, "outcome"] = "Line Achieved" if val >= row["line"] else "Line Not Achieved"
    return df


# -----------------------------------------------------------------------------
# MAIN ANALYSIS LOGIC
# -----------------------------------------------------------------------------
def run_analysis(player, stat, line, odds):
    st.write("### Running Analysis...")

    today = datetime.utcnow().date()
    season = today.year

    # --------------------------
    # NEW: Upcoming Game via NBA API
    # --------------------------
    next_game = get_upcoming_game(player)

    if not next_game:
        st.warning("No upcoming game found. Opponent-based metrics disabled.")
        opponent = None
        is_home = True
        game_id = None
        game_date = None
    else:
        game_date = next_game["parsed_date"]
        opponent = next_game["opponent_team_id"]
        is_home = next_game["is_home"]
        game_id = next_game["gameId"]

        st.info(
            f"Upcoming Game: {game_date} — "
            f"{'HOME' if is_home else 'AWAY'} vs Team {opponent}"
        )

    # ---------- Fetch season stats ----------
    stats = get_player_stats(player["id"], seasons=[season])
    df = stats_list_to_df(stats)
    if df.empty:
        st.error("No player stats found.")
        return

    field = STAT_FIELD_MAP[stat]
    df = df.sort_values("date")

    # Last 10
    last10 = df.tail(10)
    last10_avg = last10[field].mean()
    r10, h10, t10 = compute_hit_rate(last10[field], line)

    # Season
    season_avg = df[field].mean()
    rs, hs, ts = compute_hit_rate(df[field], line)

    # Home / Away
    home_mask = df["team_id"] == df["home_team_id"]
    away_mask = ~home_mask

    context_series = df[field][home_mask] if is_home else df[field][away_mask]
    context_avg = context_series.mean()
    rc, hc, tc = compute_hit_rate(context_series, line)

    # Vs Opponent
    if opponent:
        mask_vs = (df["home_team_id"] == opponent) | (df["visitor_team_id"] == opponent)
        vs_series = df[field][mask_vs]
        vs_avg = vs_series.mean()
        rv, hv, tv = compute_hit_rate(vs_series, line)
    else:
        vs_avg = 0
        rv = hv = tv = 0

    # Defensive Rating
    if opponent and game_date:
        opp_def = get_team_defensive_rating(opponent, season, game_date.isoformat())
    else:
        opp_def = 0

    projection = simple_projection(last10_avg, season_avg, context_avg, opp_def)

    # --------------------------
    # UI Cards
    # --------------------------
    st.subheader("Hit-Rate Metrics")
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        txt, col = format_percentage_card(h10, t10)
        st.metric("Last 10 ≥ Line", txt)

    with c2:
        txt, col = format_percentage_card(hs, ts)
        st.metric("Season ≥ Line", txt)

    with c3:
        txt, col = format_percentage_card(hc, tc)
        st.metric(f"{'Home' if is_home else 'Away'} ≥ Line", txt)

    with c4:
        txt, col = format_percentage_card(hv, tv)
        st.metric("Vs Opponent ≥ Line", txt)

    st.subheader("Opponent Context")
    st.metric("Opponent Defensive Rating (approx)", f"{opp_def:.1f}")

    st.subheader("Projection")
    st.metric(f"Projected {stat}", f"{projection:.1f}")

    # Save Prop
    if st.button("Save this Prop"):
        new_row = {
            "timestamp": datetime.utcnow().isoformat(),
            "player_id": player["id"],
            "player_name": f"{player['first_name']} {player['last_name']}",
            "team_abbr": player["team"]["abbreviation"],
            "stat": stat,
            "line": line,
            "odds": odds,
            "game_id": game_id,
            "game_date": game_date.isoformat() if game_date else "",
            "outcome": "Pending",
        }
        df_saved = load_saved_props()
        df_saved = pd.concat([df_saved, pd.DataFrame([new_row])], ignore_index=True)
        save_props(df_saved)
        st.success("Prop saved.")


# -----------------------------------------------------------------------------
# STREAMLIT MAIN UI
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Top Prop Picks", layout="wide")
    st.title("Top Prop Picks – NBA Prop Evaluator")

    q = st.text_input("Search Player:")
    player = None

    if q.strip():
        players = get_active_players(q)
        if players:
            names = [
                f"{p['first_name']} {p['last_name']} ({p['team']['abbreviation']})"
                for p in players
            ]
            choice = st.selectbox("Select Player:", names)
            if choice:
                player = players[names.index(choice)]

    stat = st.selectbox("Stat:", list(STAT_FIELD_MAP.keys()))
    line = st.number_input("Betting Line:", min_value=0.0, step=0.5)
    odds = st.text_input("Current Odds:")

    if player and st.button("Run Analysis", type="primary"):
        run_analysis(player, stat, line, odds)

    st.subheader("Saved Props")
    dfp = load_saved_props()
    if not dfp.empty:
        dfp = update_outcomes(dfp)
        st.dataframe(dfp)


if __name__ == "__main__":
    main()
