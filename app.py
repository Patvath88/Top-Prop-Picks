import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# -----------------------------------------------------------------------------
# Top Prop Picks Streamlit App
#
# This application allows users to evaluate NBA player prop bets by pulling
# historical and current season statistics from the balldontlie API.  Users
# select a player, choose a statistic (points, rebounds, assists, etc.), enter
# the proposed betting line and odds, and the app returns a set of
# metrics to help decide whether the bet is worthwhile.  Each metric shows
# how often the player has exceeded the selected line under different
# conditions (last 10 games, full season, home vs. away, and previous
# matchups vs. upcoming opponent).  A simple expected-value style prediction
# is also calculated.
#
# Results can be saved to a CSV file for later tracking; once a game's final
# stats are available, the app can update the record to show whether the line
# was achieved.
# -----------------------------------------------------------------------------

# Your balldontlie API key (user explicitly allowed embedding it)
BALLDONTLIE_API_KEY = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

BASE_URL = "https://api.balldontlie.io/v1"

HEADERS = {
    "Authorization": BALLDONTLIE_API_KEY
}

DATA_DIR = Path(".")
SAVED_PROPS_CSV = DATA_DIR / "saved_props.csv"

# Map human-readable stat names to balldontlie stat fields
STAT_FIELD_MAP = {
    "Points": "pts",
    "Rebounds": "reb",
    "Assists": "ast",
    "Threes Made": "fg3m",
    "Steals": "stl",
    "Blocks": "blk",
    "Turnovers": "turnover",
    "Minutes": "min",  # careful: this is a string; we convert to float minutes
}


# -----------------------------------------------------------------------------
# Utility Functions for API Calls
# -----------------------------------------------------------------------------
def api_get(endpoint: str, params: dict | None = None) -> dict:
    """Generic GET wrapper for balldontlie API with basic error handling."""
    if params is None:
        params = {}

    url = f"{BASE_URL}/{endpoint}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"API request failed: {e}")
        return {"data": []}


def get_active_players(search_term: str) -> list[dict]:
    """Search for players using balldontlie's players endpoint.

    We use the /players endpoint with a search query; this returns current and
    historical players, but in practice recent players dominate. The UI is meant
    for manual selection, so if there are duplicates, the user can pick.
    """
    if not search_term:
        return []

    params = {"search": search_term, "per_page": 50}
    data = api_get("players", params)
    players = data.get("data", [])
    # Filter out duplicate names (if any) and sort alphabetically
    unique = {}
    for p in players:
        key = (p.get("first_name", "").strip(), p.get("last_name", "").strip())
        if key not in unique:
            unique[key] = p
    return list(unique.values())


def get_player_stats(
    player_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    seasons: list[int] | None = None,
    game_ids: list[int] | None = None,
) -> list:
    """Retrieve all game stats for a player across one or more seasons.

    The API returns a maximum of 100 records per call, so we may need to
    paginate using the cursor parameter.
    """
    all_stats: list[dict] = []
    cursor = None

    while True:
        params: dict = {"player_ids[]": player_id, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if seasons:
            for s in seasons:
                params.setdefault("seasons[]", []).append(s)
        if game_ids:
            for gid in game_ids:
                params.setdefault("game_ids[]", []).append(gid)

        data = api_get("stats", params)
        stats = data.get("data", [])
        meta = data.get("meta", {})

        if not stats:
            break

        all_stats.extend(stats)

        cursor = meta.get("next_cursor")
        if not cursor:
            break

    return all_stats


def get_season_averages(player_id: int, season: int) -> dict:
    """Fetch general-base season averages for the player."""
    params = {
        "season": season,
        "season_type": "regular",
        "type": "base",
        "player_ids[]": player_id,
    }
    data = api_get("season_averages/general", params)
    records = data.get("data", [])
    if not records:
        return {}
    # Assume only one record for this player/season
    return records[0].get("stats", {})


def get_team_schedule(team_id: int, season: int) -> list[dict]:
    """Get all games for a team in a given season."""
    all_games: list[dict] = []
    cursor = None
    while True:
        params: dict = {"seasons[]": season, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor
        data = api_get("games", params)
        games = data.get("data", [])
        meta = data.get("meta", {})
        if not games:
            break
        # filter games that involve this team
        for g in games:
            if g.get("home_team_id") == team_id or g.get("visitor_team_id") == team_id:
                all_games.append(g)
        cursor = meta.get("next_cursor")
        if not cursor:
            break
    return all_games


def get_upcoming_game(player: dict) -> dict | None:
    """Get the next scheduled game for this player's team.

    The balldontlie API primarily exposes completed and scheduled games with
    dates and statuses. We look for the first game in the future for this
    player's team.
    """
    team_id = player.get("team", {}).get("id")
    if not team_id:
        return None

    # Assume current season based on today's date
    today = datetime.utcnow().date()
    # NBA seasons typically span two calendar years; for simplicity we use the
    # current year as the "season" parameter for the API.
    season = today.year

    cursor = None
    upcoming = None

    while True:
        params: dict = {
            "seasons[]": season,
            "per_page": 100,
        }
        if cursor is not None:
            params["cursor"] = cursor

        data = api_get("games", params)
        games = data.get("data", [])
        meta = data.get("meta", {})

        if not games:
            break

        for g in games:
            g_date_str = g.get("date")
            if not g_date_str:
                continue
            try:
                g_date = datetime.fromisoformat(g_date_str.split("T")[0]).date()
            except Exception:
                continue

            if g_date >= today and (
                g.get("home_team_id") == team_id or g.get("visitor_team_id") == team_id
            ):
                if upcoming is None or g_date < datetime.fromisoformat(
                    upcoming["date"].split("T")[0]
                ).date():
                    upcoming = g

        cursor = meta.get("next_cursor")
        if not cursor:
            break

    return upcoming


def get_team_defensive_rating(opponent_team_id: int, season: int, as_of_date: str) -> float:
    """Approximate opponent defensive rating as average points allowed per game.

    The balldontlie API doesn't expose a direct team defensive rating, but we can
    approximate by averaging the number of points the opponent has conceded in
    each completed game.  For games where the opponent was the home team, the
    visitor_score represents points allowed; when the opponent was the visitor,
    the home_score serves that purpose.

    Args:
        opponent_team_id: ID of the opponent team.
        season: Season to calculate average for.
        as_of_date: Only games up to this date (exclusive) are considered.

    Returns:
        Average points allowed per game (float).  Returns 0.0 if no games.
    """
    all_games: list[dict] = []
    cursor = None

    while True:
        params: dict = {
            "seasons[]": season,
            "per_page": 100,
            "end_date": as_of_date,
        }
        if cursor is not None:
            params["cursor"] = cursor

        data = api_get("games", params)
        games = data.get("data", [])
        meta = data.get("meta", {})

        if not games:
            break

        for g in games:
            if (
                g.get("home_team_id") == opponent_team_id
                or g.get("visitor_team_id") == opponent_team_id
            ):
                all_games.append(g)

        cursor = meta.get("next_cursor")
        if not cursor:
            break

    if not all_games:
        return 0.0

    total_points_allowed = 0
    count = 0
    for g in all_games:
        if g.get("status") != "Final":
            continue
        if g.get("home_team_id") == opponent_team_id:
            total_points_allowed += g.get("visitor_team_score", 0)
        elif g.get("visitor_team_id") == opponent_team_id:
            total_points_allowed += g.get("home_team_score", 0)
        count += 1

    if count == 0:
        return 0.0

    return total_points_allowed / count


# -----------------------------------------------------------------------------
# Data Transformation Helpers
# -----------------------------------------------------------------------------
def stats_list_to_df(stats: list[dict]) -> pd.DataFrame:
    """Convert API stats list to a normalized DataFrame."""
    if not stats:
        return pd.DataFrame()

    rows = []
    for s in stats:
        game = s.get("game", {})
        team = s.get("team", {})
        row = {
            "game_id": game.get("id"),
            "date": game.get("date"),
            "season": game.get("season"),
            "home_team_id": game.get("home_team_id"),
            "visitor_team_id": game.get("visitor_team_id"),
            "home_team_score": game.get("home_team_score"),
            "visitor_team_score": game.get("visitor_team_score"),
            "team_id": team.get("id"),
            "team_abbr": team.get("abbreviation"),
        }
        # Add base stats
        for field in [
            "pts",
            "reb",
            "ast",
            "fg3m",
            "stl",
            "blk",
            "turnover",
            "min",
        ]:
            row[field] = s.get(field)
        rows.append(row)

    df = pd.DataFrame(rows)
    # Convert date to datetime
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"].str[:10], errors="coerce")
    # Convert minutes string "32" or "32:15" to float minutes
    if "min" in df.columns:
        def _to_minutes(x):
            if x is None or x == "" or pd.isna(x):
                return 0.0
            if isinstance(x, (int, float)):
                return float(x)
            if isinstance(x, str):
                if ":" in x:
                    parts = x.split(":")
                    try:
                        return float(parts[0]) + float(parts[1]) / 60.0
                    except Exception:
                        return 0.0
                try:
                    return float(x)
                except Exception:
                    return 0.0
            return 0.0

        df["min"] = df["min"].apply(_to_minutes)
    return df


def compute_hit_rate(series: pd.Series, line_value: float) -> tuple[float, int, int]:
    """Compute how often a stat series beats or equals the betting line.

    Returns:
        (hit_rate, hits, total) where hit_rate is a float between 0 and 1.
    """
    clean = series.dropna()
    if clean.empty:
        return 0.0, 0, 0
    hits = (clean >= line_value).sum()
    total = len(clean)
    hit_rate = hits / total if total > 0 else 0.0
    return hit_rate, hits, total


def format_percentage_card(hits: int, total: int) -> tuple[str, str]:
    """Format text and CSS color for a metric card."""
    if total == 0:
        display = "0/0 (0%)"
        color = "red"
    else:
        pct = hits / total
        pct_str = f"{pct*100:.0f}%"
        display = f"{hits}/{total} ({pct_str})"
        if pct > 0.70:
            color = "green"
        elif pct >= 0.50:
            color = "gold"
        else:
            color = "red"
    return display, color


def render_metric_card(label: str, display_text: str, color: str):
    """Render a colored bordered metric card with Streamlit HTML."""
    border_color = {
        "green": "#2ecc71",
        "gold": "#f1c40f",
        "red": "#e74c3c",
    }.get(color, "#bdc3c7")

    st.markdown(
        f"""
        <div style="
            border: 2px solid {border_color};
            border-radius: 10px;
            padding: 10px 14px;
            margin: 4px;
            background-color: #ffffff;
        ">
            <div style="font-size: 12px; color: #7f8c8d; text-transform: uppercase;">
                {label}
            </div>
            <div style="font-size: 18px; font-weight: 700; color: #2c3e50;">
                {display_text}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def simple_projection(
    last10_avg: float,
    season_avg: float,
    home_away_avg: float,
    opp_def_rating: float,
) -> float:
    """Generate a simple predicted stat total for the next game.

    This is intentionally transparent and heuristic rather than a black-box ML
    model. We blend:
      - last 10 average (weight 0.5)
      - season average (weight 0.3)
      - context (home/away) average (weight 0.2)
    Then apply a small adjustment for opponent defense (lower if they allow
    fewer points than league average, higher if more).

    For non-points stats (reb, ast, etc.) we still apply the same adjustment,
    acknowledging it's a coarse approximation.
    """
    baseline = (
        0.5 * last10_avg +
        0.3 * season_avg +
        0.2 * home_away_avg
    )

    # Defensive adjustment: assume league average points allowed ~ 114
    league_avg_points_allowed = 114.0
    if opp_def_rating > 0:
        diff = league_avg_points_allowed - opp_def_rating
        # Each point of defensive rating difference shifts projection ~0.5%
        adj_factor = 1 + (diff / league_avg_points_allowed) * 0.5
    else:
        adj_factor = 1.0

    return baseline * adj_factor


# -----------------------------------------------------------------------------
# Persistence for Saved Props
# -----------------------------------------------------------------------------
def load_saved_props() -> pd.DataFrame:
    if not SAVED_PROPS_CSV.exists():
        return pd.DataFrame(
            columns=[
                "timestamp",
                "player_id",
                "player_name",
                "team_abbr",
                "stat",
                "line",
                "odds",
                "last10_display",
                "season_display",
                "vs_opponent_display",
                "home_away_display",
                "opp_def_rating",
                "projection",
                "game_id",
                "game_date",
                "outcome",
            ]
        )
    try:
        df = pd.read_csv(SAVED_PROPS_CSV)
    except Exception:
        df = pd.DataFrame()
    return df


def save_props(df: pd.DataFrame):
    df.to_csv(SAVED_PROPS_CSV, index=False)


def update_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Update 'outcome' column by checking final game stats if still pending."""
    if df.empty:
        return df

    pending = df[df["outcome"] == "Pending"]
    if pending.empty:
        return df

    for idx, row in pending.iterrows():
        game_id = row.get("game_id")
        player_id = row.get("player_id")
        stat = row.get("stat")
        line = row.get("line")
        if pd.isna(game_id) or pd.isna(player_id) or pd.isna(line):
            continue

        # Fetch stats for that game and player
        stats = get_player_stats(int(player_id), game_ids=[int(game_id)])
        if not stats:
            continue

        df_stats = stats_list_to_df(stats)
        field = STAT_FIELD_MAP.get(stat)
        if not field or field not in df_stats.columns:
            continue

        final_value = float(df_stats[field].iloc[0])
        if final_value >= float(line):
            outcome = "Line Achieved"
        else:
            outcome = "Line Not Achieved"

        df.at[idx, "outcome"] = outcome

    return df


# -----------------------------------------------------------------------------
# Streamlit UI
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Top Prop Picks", layout="wide")

    st.title("Top Prop Picks – NBA Prop Evaluator")

    st.markdown(
        """
        Use this tool to sanity-check an NBA player prop before you bet it.

        1. Search and select a player.  
        2. Choose a stat type and enter the line + odds.  
        3. The app pulls historical and situational data from balldontlie and
           displays hit-rates in color-coded cards.  
        4. A simple projection estimates the expected stat total for the next game.  
        5. Save the prop so you can track later whether the line was actually hit.
        """
    )

    # --- Player Search ---
    search_term = st.text_input("Search Player (first or last name):", value="")

    players = []
    selected_player = None
    if search_term.strip():
        players = get_active_players(search_term.strip())
        if players:
            player_options = [
                f'{p["first_name"]} {p["last_name"]} ({p["team"]["abbreviation"]})'
                for p in players
            ]
            chosen = st.selectbox("Select Player:", options=player_options)
            if chosen:
                idx = player_options.index(chosen)
                selected_player = players[idx]
        else:
            st.warning("No players found for that search term.")

    # --- Stat selection and line input ---
    stat = st.selectbox("Stat Type:", options=list(STAT_FIELD_MAP.keys()))
    line_value = st.number_input(
        "Betting Line (number you are betting over/under):", min_value=0.0, step=0.5
    )
    odds_value = st.text_input("Current Odds for this line (e.g. -115):", value="")

    if selected_player:
        st.markdown("---")
        st.subheader(
            f"Analysis for {selected_player['first_name']} "
            f"{selected_player['last_name']} – {stat}"
        )

        if st.button("Run Analysis", type="primary"):
            run_analysis(selected_player, stat, line_value, odds_value)

    st.markdown("---")
    st.subheader("Saved Props & Results")
    props_df = load_saved_props()
    if not props_df.empty:
        # Try to update pending outcomes
        with st.spinner("Updating outcomes for saved props..."):
            updated_df = update_outcomes(props_df.copy())
            if not updated_df.equals(props_df):
                save_props(updated_df)
                props_df = updated_df
        st.dataframe(props_df)
    else:
        st.info("No props saved yet. Run an analysis and click 'Save Prop' to log one.")


def run_analysis(player: dict, stat: str, line_value: float, odds_value: str):
    player_id = player["id"]
    team = player.get("team", {})
    team_id = team.get("id")
    team_abbr = team.get("abbreviation")
    today = datetime.utcnow().date()
    season = today.year

    # --- Get upcoming game (for opponent and context) ---
    upcoming_game = get_upcoming_game(player)
    if not upcoming_game:
        st.warning("No upcoming game found for this player/team. We'll still use season data.")
        opponent_team_id = None
        is_home = True
        game_id = None
        game_date = None
    else:
        game_id = upcoming_game.get("id")
        game_date_str = upcoming_game.get("date", "")[:10]
        try:
            game_date = datetime.fromisoformat(game_date_str).date()
        except Exception:
            game_date = None

        if upcoming_game.get("home_team_id") == team_id:
            opponent_team_id = upcoming_game.get("visitor_team_id")
            is_home = True
        else:
            opponent_team_id = upcoming_game.get("home_team_id")
            is_home = False

        st.info(
            f"Next game: {game_date_str} – "
            f"{'HOME vs' if is_home else 'AWAY at'} "
            f"Team ID {opponent_team_id}"
        )

    # --- Fetch player stats for this season ---
    with st.spinner("Fetching player game stats for this season..."):
        stats = get_player_stats(player_id, seasons=[season])

    df = stats_list_to_df(stats)
    if df.empty:
        st.error("No stats found for this player/season.")
        return

    field = STAT_FIELD_MAP.get(stat)
    if field not in df.columns:
        st.error(f"Stat field '{field}' not available in retrieved data.")
        return

    # Last 10 games
    df_sorted = df.sort_values("date")
    last10 = df_sorted.tail(10)
    last10_series = last10[field]
    last10_avg = last10_series.mean() if not last10_series.empty else 0.0
    last10_hit_rate, last10_hits, last10_total = compute_hit_rate(last10_series, line_value)

    # Full season
    season_series = df_sorted[field]
    season_avg = season_series.mean() if not season_series.empty else 0.0
    season_hit_rate, season_hits, season_total = compute_hit_rate(season_series, line_value)

    # Home vs away
    if is_home:
        home_mask = df_sorted["team_id"] == df_sorted["home_team_id"]
        away_mask = df_sorted["team_id"] != df_sorted["home_team_id"]
    else:
        # from player's perspective: home if team_id == home_team_id
        home_mask = df_sorted["team_id"] == df_sorted["home_team_id"]
        away_mask = ~home_mask

    home_series = df_sorted.loc[home_mask, field]
    away_series = df_sorted.loc[away_mask, field]

    if is_home:
        context_label = "Season Home Games"
        context_series = home_series
    else:
        context_label = "Season Away Games"
        context_series = away_series

    context_avg = context_series.mean() if not context_series.empty else 0.0
    context_hit_rate, context_hits, context_total = compute_hit_rate(
        context_series, line_value
    )

    # Versus upcoming opponent historical
    if opponent_team_id is not None:
        mask_vs_opp = (df_sorted["home_team_id"] == opponent_team_id) | (
            df_sorted["visitor_team_id"] == opponent_team_id
        )
        vs_opp_series = df_sorted.loc[mask_vs_opp, field]
        vs_opp_avg = vs_opp_series.mean() if not vs_opp_series.empty else 0.0
        vs_opp_hit_rate, vs_opp_hits, vs_opp_total = compute_hit_rate(
            vs_opp_series, line_value
        )
    else:
        vs_opp_series = pd.Series(dtype=float)
        vs_opp_avg = 0.0
        vs_opp_hit_rate, vs_opp_hits, vs_opp_total = 0.0, 0, 0

    # Opponent defensive rating approximation
    if opponent_team_id is not None and game_date is not None:
        with st.spinner("Estimating opponent defensive rating..."):
            opp_def_rating = get_team_defensive_rating(
                opponent_team_id, season, game_date.isoformat()
            )
    else:
        opp_def_rating = 0.0

    # Simple projection
    projection = simple_projection(
        last10_avg,
        season_avg,
        context_avg,
        opp_def_rating,
    )

    # --- Display metric cards ---
    st.markdown("#### Key Hit-Rate Metrics")

    cols = st.columns(4)

    # Last 10
    last10_display, last10_color = format_percentage_card(last10_hits, last10_total)
    with cols[0]:
        render_metric_card("Last 10 Games ≥ Line", last10_display, last10_color)
        st.caption(f"Avg: {last10_avg:.1f} {STAT_FIELD_MAP[stat]}")

    # Season
    season_display, season_color = format_percentage_card(season_hits, season_total)
    with cols[1]:
        render_metric_card("Season ≥ Line", season_display, season_color)
        st.caption(f"Avg: {season_avg:.1f} {STAT_FIELD_MAP[stat]}")

    # Context (home/away)
    context_display, context_color = format_percentage_card(context_hits, context_total)
    with cols[2]:
        render_metric_card(f"{context_label} ≥ Line", context_display, context_color)
        st.caption(f"Avg: {context_avg:.1f} {STAT_FIELD_MAP[stat]}")

    # Versus opponent
    vs_opp_display, vs_opp_color = format_percentage_card(vs_opp_hits, vs_opp_total)
    with cols[3]:
        render_metric_card("Vs Upcoming Opponent ≥ Line", vs_opp_display, vs_opp_color)
        st.caption(f"Avg: {vs_opp_avg:.1f} {STAT_FIELD_MAP[stat]}")

    # Opponent defensive rating and projection
    st.markdown("#### Opponent Context & Projection")
    c1, c2 = st.columns(2)
    with c1:
        if opp_def_rating > 0:
            st.metric(
                "Opponent Defensive Rating (approx. pts allowed/game)",
                f"{opp_def_rating:.1f}",
            )
        else:
            st.metric("Opponent Defensive Rating", "N/A")
    with c2:
        st.metric(
            f"Projected {stat} Next Game",
            f"{projection:.1f}",
            help="Heuristic blend of last 10, season, home/away, and opponent defense.",
        )

    # --- Save Prop ---
    if st.button("Save this Prop for Tracking"):
        props_df = load_saved_props()
        new_row = {
            "timestamp": datetime.utcnow().isoformat(),
            "player_id": player_id,
            "player_name": f"{player['first_name']} {player['last_name']}",
            "team_abbr": team_abbr,
            "stat": stat,
            "line": line_value,
            "odds": odds_value,
            "last10_display": last10_display,
            "season_display": season_display,
            "vs_opponent_display": vs_opp_display,
            "home_away_display": context_display,
            "opp_def_rating": opp_def_rating,
            "projection": projection,
            "game_id": game_id,
            "game_date": game_date.isoformat() if game_date else "",
            "outcome": "Pending",
        }
        props_df = pd.concat([props_df, pd.DataFrame([new_row])], ignore_index=True)
        save_props(props_df)
        st.success("Prop saved for tracking.")


if __name__ == "__main__":
    main()
