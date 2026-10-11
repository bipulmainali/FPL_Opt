                else:
                    st.dataframe(recs, use_container_width=True, hide_index=True)
                    st.caption("One-for-one legal swaps only. Net spend uses estimated selling prices. Projection gain is a heuristic; hit treatment is indicative and should be checked against your actual free-transfer rules.")
            st.markdown("#### Transfer history")
            if isinstance(transfers, list) and transfers:
                tdf = pd.DataFrame(transfers)
                if "time" in tdf:
                    tdf["time"] = pd.to_datetime(tdf["time"], errors="coerce", utc=True)
                st.dataframe(tdf, use_container_width=True, hide_index=True)
            else:
                st.write("No public transfer history returned.")

    with tab_chips:
        st.subheader("Chip strategy")
        if not squad.empty:
            if chips:
                chip_df = pd.DataFrame(chips)
                st.markdown("**Chips recorded by FPL**")
                st.dataframe(chip_df, use_container_width=True, hide_index=True)
            else:
                st.caption("No chip history was returned by the API.")
            st.markdown("**Chip assessment**")
            st.write("Select a chip to view the relevant decision checklist.")
            chip_choice = st.selectbox("Chip", ["Free Hit", "Wildcard", "Bench Boost", "Triple Captain"])
            used_names = [str(c.get("name", "")).lower() for c in chips if isinstance(c, dict)]
            chip_alias = {"Free Hit": "freehit", "Wildcard": "wildcard", "Bench Boost": "bboost", "Triple Captain": "3xc"}
            is_used = any(chip_alias[chip_choice] in x for x in used_names)
            if is_used:
                st.warning(f"The public history suggests {chip_choice} has already been used. Confirm chip rules for this season.")
            else:
                if chip_choice == "Free Hit":
                    st.write("Compare the best optimized XI with your current XI. Free Hit is most attractive when the one-week gain is substantial and the squad returns to normal afterward.")
                elif chip_choice == "Wildcard":
                    st.write("Use when multiple squad problems persist over several gameweeks. Compare the optimized multiweek squad against a small number of transfers and account for future flexibility.")
                elif chip_choice == "Bench Boost":
                    bench = squad[squad["pick_position"] > 11] if "pick_position" in squad else squad.iloc[11:]
                    st.metric("Current bench projection", round(float(bench[projection_col].sum()), 2))
                    st.write("Bench Boost is most attractive when all four bench players have strong minutes security and good fixtures, particularly in a double gameweek.")
                else:
                    xi, _ = best_starting_xi(squad, projection_col)
                    if xi is not None:
                        caps = rank_captains(xi, projection_col)
                        st.dataframe(caps[["name", "team", projection_col, "availability", "fixture_run"]].rename(columns={
                            "name":"Captain candidate", "team":"Club", projection_col:"Projection", "availability":"Availability", "fixture_run":"Fixtures"
                        }), use_container_width=True, hide_index=True)
                    st.write("Triple Captain usually has greater upside in a double gameweek with high expected minutes, but is not automatically optimal.")
            st.markdown("#### Automated signal (heuristic)")
            optimized = st.session_state.get("optimized_squad")
            if optimized is not None:
                for label, value in chip_assessment(squad, optimized, fixtures, team_map, selected_gw, horizon, projection_col):
                    st.write(f"**{label}:** {value}")
            else:
                st.info("Run the Squad Optimizer first to compare your current team with an optimized Free Hit squad.")
        else:
            st.info("Load your team to assess chip usage and chip value.")

    with tab_players:
        st.subheader("Player database")
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            pos_filter = st.multiselect("Position", ["GK", "DEF", "MID", "FWD"], default=["GK", "DEF", "MID", "FWD"], key="pos_filter")
        with f2:
            club_filter = st.multiselect("Club", sorted(projected["team"].unique()), key="club_filter")
        with f3:
            max_price = st.slider("Maximum price (£m)", 3.5, 15.5, 15.5, 0.1)
        with f4:
            min_minutes = st.number_input("Minimum minutes", 0, 4000, 0, 90)
        min_projection = st.slider("Minimum horizon projection", 0.0, 50.0, 0.0, 0.5)
        view = projected[
            projected["position"].isin(pos_filter)
            & (projected["price"] <= money_input_to_tenths(max_price))
            & (projected["minutes"] >= min_minutes)
            & (projected["xP_horizon"] >= min_projection)
        ].copy()
        if club_filter:
            view = view[view["team"].isin(club_filter)]
        view = view.sort_values("xP_horizon", ascending=False)
        view["Price"] = view["price"].map(money)
        cols = ["name", "team", "position", "Price", "form", "ppg", "points", "xP_next", "xP_horizon",
                "value_per_m", "selected_by", "availability", "news", "fixture_run"]
        st.dataframe(view[cols].rename(columns={
            "name":"Player", "team":"Club", "position":"Pos", "form":"Form", "ppg":"Pts/G",
            "points":"Total pts", "xP_next":"xP next GW", "xP_horizon":"xP horizon",
            "value_per_m":"xP per £m", "selected_by":"Selected %", "availability":"Availability",
            "news":"FPL news", "fixture_run":"Fixtures"
        }), use_container_width=True, hide_index=True)

    with tab_history:
        st.subheader("Manager history")
        if not profile:
            st.info("Manager history will appear after team ID 533979 loads successfully.")
        else:
            h1, h2, h3 = st.columns(3)
            h1.metric("Overall rank", f'{profile.get("summary_overall_rank"):,}' if profile.get("summary_overall_rank") else "—")
            h2.metric("Overall points", f'{profile.get("summary_overall_points"):,}' if profile.get("summary_overall_points") is not None else "—")
            h3.metric("Team value", money(profile.get("last_deadline_value", 0)))
            current_hist = history.get("current", []) if history else []
            if current_hist:
                hdf = pd.DataFrame(current_hist)
                keep = [c for c in ["event", "points", "total_points", "rank", "overall_rank", "event_transfers", "event_transfers_cost", "points_on_bench", "value", "bank"] if c in hdf]
                st.dataframe(hdf[keep].rename(columns={
                    "event":"GW", "points":"GW points", "total_points":"Total points", "rank":"GW rank",
                    "overall_rank":"Overall rank", "event_transfers":"Transfers", "event_transfers_cost":"Hit",
                    "points_on_bench":"Bench points", "value":"Team value (tenths)", "bank":"Bank (tenths)"
                }), use_container_width=True, hide_index=True)
                if "points" in hdf:
                    st.line_chart(hdf.set_index("event")["points"])
            past = history.get("past", []) if history else []
            if past:
                st.markdown("**Previous seasons**")
                st.dataframe(pd.DataFrame(past), use_container_width=True, hide_index=True)

    with st.expander("Model details and important limitations"):
        st.markdown("""
**Data source:** public endpoints at `https://fantasy.premierleague.com/api/`. This API is unofficial, undocumented by FPL, and can change or throttle requests.

**Optimization constraints:** exactly 15 players; 2 goalkeepers, 5 defenders, 5 midfielders and 3 forwards; no more than 3 players from one club; total player cost must fit the budget. The best XI enumerator enforces a goalkeeper and legal 3–5 defenders, 2–5 midfielders and 1–3 forwards.

**Projection method:** blends FPL's `ep_next`, points per game and recent form, then applies a modest fixture difficulty modifier across the chosen horizon. Availability flags reduce projections. These are heuristic projections, not calibrated probabilities.

**Prices:** current prices are public. Selling prices are taken from the public picks payload if present, otherwise current price is used as a fallback. Price-rise profit rules and purchase history may therefore be imperfect.

**Transfers:** the transfer shortlist evaluates legal one-for-one moves. It does not yet search every multi-transfer sequence or model price changes. Free transfers and points-hit inputs are editable because the API's public endpoints do not always expose the exact live account state.

**Chips:** chip history is read from public manager history when available. The chip value assessment is a comparison/checklist, not a precise expected-value simulation. Check current season rules and official team news.

**Privacy:** the app does not ask for your FPL password, cookies, or login credentials.
        """)
