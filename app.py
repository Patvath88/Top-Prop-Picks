9    proj = projection(l10_avg, season_avg, home_avg, def_rating)

    # -------------------------------------------------
    # METRIC CARD GRID
    # -------------------------------------------------

    st.markdown("### Hit Rates & Context")

    c1, c2, c3 = st.columns(3)
    with c1: metric_card("Last 10 ≥ Line", h10, t10, l10_avg)
    with c2: metric_card("Season ≥ Line", hs, ts, season_avg)
    with c3: metric_card("Home ≥ Line", hh, th, home_avg)

    c4, c5, c6 = st.columns(3)
    with c4: metric_card("Away ≥ Line", ha, ta, away_avg)
    with c5: metric_card("Vs Opponent ≥ Line", hv, tv, vs_avg)
    with c6: metric_card("Opponent Def Rating", 0, 0, def_rating)

    st.subheader("Projection")
    st.metric(f"Projected {stat}", f"{proj:.1f}")

    # SAVE PROP
    if st.button("Save This Prop"):
        dfp = load_props()

        new = {
            "timestamp": datetime.utcnow().isoformat(),
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
        dfp = dfp.sort_values("timestamp", ascending=False)
        st.dataframe(dfp, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
