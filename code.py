"""
FPL Team Advisor — Streamlit
============================
Features:
- Public Fantasy Premier League API data (no password/API key)
- Team import, current squad, budget, selling prices, transfer history and chip history
- Multi-gameweek projections with fixture-difficulty adjustment
- Legal 15-player squad optimization (budget, position quotas, max 3 per club)
- Best starting XI / formation and captain recommendations
- Transfer shortlist with budget, free-transfer and points-hit inputs
- Free Hit, Wildcard, Bench Boost and Triple Captain decision support
- Player browser, injury flags, fixture run, price-change indicators
- Defensive API parsing and 15-minute caching

Projection disclaimer:
This is a transparent heuristic, not an official FPL model or a guarantee of points.
The public API is unofficial and can change. Verify team news before the deadline.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import pandas as pd
import requests
import streamlit as st

try:
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix
    SCIPY_AVAILABLE = True
except Exception:
    SCIPY_AVAILABLE = False

API = "https://fantasy.premierleague.com/api"
TIMEOUT = 25
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
POSITION_QUOTAS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
SQUAD_SIZE = 15
MAX_PER_CLUB = 3

st.set_page_config(page_title="FPL Team Advisor", page_icon="⚽", layout="wide")


# ----------------------------- API ------------------------------------------

@st.cache_data(ttl=900, show_spinner=False)
def api_get(path: str) -> Any:
    """Fetch and cache public FPL API JSON."""
    url = f"{API}/{path.lstrip('/')}"
    response = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 FPL-Team-Advisor/2.0"},
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=900, show_spinner=False)
def load_core_data():
    bootstrap = api_get("bootstrap-static/")
    fixtures = api_get("fixtures/")
    teams = {int(t["id"]): t for t in bootstrap.get("teams", [])}
    events = bootstrap.get("events", [])
    elements = bootstrap.get("elements", [])

    rows = []
    for p in elements:
        team_id = int(p.get("team", 0))
        status = p.get("status", "")
        chance = p.get("chance_of_playing_next_round")
        rows.append({
            "id": int(p["id"]),
            "name": p.get("web_name") or p.get("second_name") or f'Player {p["id"]}',
            "full_name": f'{p.get("first_name", "")} {p.get("second_name", "")}'.strip(),
            "team_id": team_id,
            "team": teams.get(team_id, {}).get("name", "Unknown"),
            "position": POSITION_NAMES.get(int(p.get("element_type", 0)), "?"),
            "price": int(p.get("now_cost", 0)),  # tenths of £m
            "form": safe_float(p.get("form")),
            "points": safe_float(p.get("total_points")),
            "ppg": safe_float(p.get("points_per_game")),
            "minutes": int(p.get("minutes") or 0),
            "selected_by": safe_float(p.get("selected_by_percent")),
            "status": status,
            "chance": chance,
            "news": p.get("news") or "",
            "ict": safe_float(p.get("ict_index")),
            "xgi": safe_float(p.get("expected_goal_involvements")),
            "ep_next": safe_float(p.get("ep_next")),
            "ep_this": safe_float(p.get("ep_this")),
            "value": safe_float(p.get("value_season")),
            "transfers_in_event": int(p.get("transfers_in_event") or 0),
            "transfers_out_event": int(p.get("transfers_out_event") or 0),
            "price_change:": int(p.get("cost_change_start") or 0),
            "cost_change_event": int(p.get("cost_change_event") or 0),
            "starts": int(p.get("starts") or 0),
            "goals": int(p.get("goals_scored") or 0),
            "assists": int(p.get("assists") or 0),
            "clean_sheets": int(p.get("clean_sheets") or 0),
            "bonus": int(p.get("bonus") or 0),
            "yellow_cards": int(p.get("yellow_cards") or 0),
            "red_cards": int(p.get("red_cards") or 0),
            "penalties_order": p.get("penalties_order"),
            "corners_order": p.get("corners_and_indirect_freekicks_order"),
            "direct_fk_order": p.get("direct_freekicks_order"),
        })

    players = pd.DataFrame(rows)
    return bootstrap, fixtures, teams, pd.DataFrame(rows)


def safe_float(value, default=0.0) -> float:
    try:
        if value in (None, "", "-", "None"):
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def money(tenths: int | float) -> str:
    return f"£{float(tenths) / 10:.1f}m"


def money_input_to_tenths(value: float) -> int:
    return int(round(value * 10))


def event_options(events):
    return [e for e in events if not e.get("finished")]


def default_event_id(events):
    for e in events:
        if e.get("is_next"):
            return int(e["id"])
    for e in events:
        if e.get("is_current") and not e.get("finished"):
            return int(e["id"])
    for e in events:
        if not e.get("finished"):
            return int(e["id"])
    return max([int(e.get("id", 1)) for e in events] or [1])


# ----------------------------- Fixtures / projections ------------------------

def fixtures_for_team(fixtures, team_id: int, start_gw: int, horizon: int, teams):
    """Return future fixtures for team, including multiple fixtures in a GW."""
    out = []
    for f in fixtures:
        gw = f.get("event")
        if gw is None or int(gw) < start_gw or int(gw) >= start_gw + horizon:
            continue
        if int(f.get("team_h", -1)) == team_id:
            opponent_id = int(f["team_a"])
            out.append({
                "gw": int(gw), "opponent": teams.get(opponent_id, {}).get("name", "?"),
                "venue": "H", "fdr": int(f.get("team_h_difficulty") or 3),
                "started": bool(f.get("started")), "finished": bool(f.get("finished")),
            })
        elif int(f.get("team_a", -1)) == team_id:
            opponent_id = int(f["team_h"])
            out.append({
                "gw": int(gw), "opponent": teams.get(opponent_id, {}).get("name", "?"),
                "venue": "A", "fdr": int(f.get("team_a_difficulty") or 3),
                "started": bool(f.get("started")), "finished": bool(f.get("finished")),
            })
    return sorted(out, key=lambda x: (x["gw"], x["venue"]))


def fixture_label(fixtures, team_id, start_gw, horizon, teams):
    fs = fixtures_for_team(fixtures, int(team_id), start_gw, horizon, teams)
    if not fs:
        return "No scheduled fixture"
    by_gw = {}
    for f in fs:
        by_gw.setdefault(f["gw"], []).append(f"{f['opponent']} ({f['venue']}, FDR {f['fdr']})")
    return " · ".join(f"GW{gw}: {', '.join(names)}" for gw, names in by_gw.items())


FDR_COLORS = {1: "#006400", 2: "#70AD47", 3: "#F2F2F2", 4: "#E57373", 5: "#8B0000"}
FDR_TEXT_COLORS = {1: "#FFFFFF", 2: "#14210D", 3: "#333333", 4: "#321010", 5: "#FFFFFF"}

def fixture_difficulty_table(squad, fixtures, start_gw, horizon, teams):
    """Build one row per squad player and scheduled fixture for color-coded FDR display."""
    rows = []
    if squad.empty:
        return pd.DataFrame(columns=["Player", "Club", "GW", "Opponent", "Venue", "FDR"])
    for _, player in squad.iterrows():
        for fx in fixtures_for_team(fixtures, int(player.get("team_id", 0)), start_gw, horizon, teams):
            rows.append({
                "Player": player.get("name", ""), "Club": player.get("team", ""),
                "GW": fx["gw"], "Opponent": fx["opponent"],
                "Venue": "Home" if fx["venue"] == "H" else "Away", "FDR": int(fx["fdr"]),
            })
    return pd.DataFrame(rows, columns=["Player", "Club", "GW", "Opponent", "Venue", "FDR"])

def style_fdr(value):
    try:
        rating = max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return ""
    return f"background-color: {FDR_COLORS[rating]}; color: {FDR_TEXT_COLORS[rating]}; font-weight: 700; text-align: center"


def availability_multiplier(row) -> float:
    status = str(row.get("status", ""))
    chance = row.get("chance")
    if status in {"i", "s", "u", "n"}:
        return 0.10
    if chance is not None and not pd.isna(chance):
        try:
            return max(0.15, min(1.0, float(chance) / 100))
        except (ValueError, TypeError):
            pass
    return 1.0


def player_base_projection(row) -> float:
    """Blend FPL ep_next, PPG and recent form; reduce for low minutes/availability."""
    ep = max(0.0, safe_float(row.get("ep_next")))
    ppg = max(0.0, safe_float(row.get("ppg")))
    form = max(0.0, safe_float(row.get("form")))
    if ep > 0:
        raw = 0.55 * ep + 0.25 * ppg + 0.20 * form
    else:
        raw = 0.55 * ppg + 0.45 * form
    minutes = safe_float(row.get("minutes"))
    if minutes < 180:
        raw *= max(0.35, minutes / 180)
    return raw * availability_multiplier(row)


def fixture_multiplier(fdr: int) -> float:
    """Small, bounded fixture adjustment; neutral at FDR 3."""
    return max(0.78, min(1.22, 1.0 + (3 - int(fdr)) * 0.055))


def projected_points(row, fixtures, start_gw, horizon, teams) -> float:
    """Project a player's horizon using baseline xP and per-fixture difficulty."""
    base = player_base_projection(row)
    team_id = int(row.get("team_id", 0))
    fs = fixtures_for_team(fixtures, team_id, start_gw, horizon, teams)
    if not fs:
        return 0.0
    total = 0.0
    for f in fs:
        # Approximate a normal GW baseline. More than one fixture increases total.
        total += base * fixture_multiplier(f["fdr"])
    return round(total, 2)


def add_projections(players, fixtures, start_gw, horizon, teams):
    df = players.copy()
    df["xP_next"] = df.apply(player_base_projection, axis=1).round(2)
    df["xP_horizon"] = df.apply(
        lambda r: projected_points(r, fixtures, start_gw, horizon, teams), axis=1
    )
    df["fixture_run"] = df.apply(
        lambda r: fixture_label(fixtures, r["team_id"], start_gw, horizon, teams), axis=1
    )
    df["availability"] = df.apply(availability_label, axis=1)
    df["value_per_m"] = df.apply(
        lambda r: round(r["xP_horizon"] / max(0.1, r["price"] / 10), 2), axis=1
    )
    return df


def availability_label(row):
    status = str(row.get("status", ""))
    chance = row.get("chance")
    if status in {"i", "s", "u", "n"}:
        return "Unavailable / flagged"
    if chance is not None and not pd.isna(chance) and safe_float(chance, 100) < 75:
        return "Doubtful"
    if status == "d" or (chance is not None and not pd.isna(chance) and safe_float(chance, 100) < 100):
        return "Monitor"
    return "Likely available"


# ----------------------------- Team import ----------------------------------

@st.cache_data(ttl=900, show_spinner=False)
def load_manager(manager_id: int):
    profile = api_get(f"entry/{manager_id}/")
    history = api_get(f"entry/{manager_id}/history/")
    transfers = api_get(f"entry/{manager_id}/transfers/")
    return profile, history, transfers


@st.cache_data(ttl=900, show_spinner=False)
def load_picks(manager_id: int, gw: int):
    return api_get(f"entry/{manager_id}/event/{gw}/picks/")


def get_team_picks(manager_id, selected_gw, events):
    """Try selected GW, then latest finished GW if upcoming picks aren't public yet."""
    attempts = [selected_gw]
    finished = [int(e["id"]) for e in events if e.get("finished")]
    attempts += sorted([gw for gw in finished if gw != selected_gw], reverse=True)
    errors = []
    for gw in attempts:
        try:
            payload = load_picks(manager_id, gw)
            if payload.get("picks"):
                return payload, gw
        except requests.RequestException as exc:
            errors.append(str(exc))
    raise RuntimeError("Could not load public picks for this team/gameweek. " + " ".join(errors[-2:]))


def build_squad(picks_payload, players):
    lookup = players.set_index("id").to_dict("index")
    rows = []
    for pick in picks_payload.get("picks", []):
        pid = int(pick.get("element", 0))
        if pid not in lookup:
            continue
        r = dict(lookup[pid])
        r["pick_position"] = int(pick.get("position", 99))
        r["multiplier"] = int(pick.get("multiplier", 1))
        r["is_captain"] = bool(pick.get("is_captain"))
        r["is_vice_captain"] = bool(pick.get("is_vice_captain"))
        r["purchase_price"] = int(pick.get("purchase_price", r["price"]))
        # The public picks payload may include selling_price; fall back to current price if not.
        r["selling_price"] = int(pick.get("selling_price", r["price"]))
        r["is_starting"] = r["pick_position"] <= 11
        rows.append(r)
    return pd.DataFrame(rows)


def infer_bank_tenths(profile, history, picks_payload):
    # Latest visible entry history bank is usually in tenths of £m.
    entry_hist = picks_payload.get("entry_history") or {}
    if entry_hist.get("bank") is not None:
        return int(entry_hist["bank"])
    current = history.get("current") or []
    if current and current[-1].get("bank") is not None:
        return int(current[-1]["bank"])
    # Do not pretend the bank is zero when the public API omits it.
    return None


def used_chips(history):
    return history.get("chips") or []


# ----------------------------- Optimization ---------------------------------

def legal_squad(df: pd.DataFrame, budget_tenths: int) -> bool:
    if len(df) != 15 or df["id"].nunique() != 15:
        return False
    if int(df["price"].sum()) > budget_tenths:
        return False
    for pos, count in POSITION_QUOTAS.items():
        if int((df["position"] == pos).sum()) != count:
            return False
    return bool((df.groupby("team_id").size() <= MAX_PER_CLUB).all())


def optimize_squad_milp(players: pd.DataFrame, budget_tenths: int, projection_col: str):
    """Maximize horizon projection for a legal 15-player squad using binary MILP."""
    if not SCIPY_AVAILABLE:
        return None, "Install scipy to enable the full squad optimizer."
    candidates = players.copy().reset_index(drop=True)
    n = len(candidates)
    if n < 15:
        return None, "Not enough player records to optimize."
    # Objective minimizes negative projected points.
    objective = -candidates[projection_col].astype(float).to_numpy()
    constraints = []
    lower = []
    upper = []
    # Exactly 15 players.
    constraints.append([1.0] * n); lower.append(15); upper.append(15)
    # Position quotas.
    for pos, count in POSITION_QUOTAS.items():
        constraints.append((candidates["position"] == pos).astype(float).tolist())
        lower.append(count); upper.append(count)
    # Budget.
    constraints.append(candidates["price"].astype(float).tolist())
    lower.append(-float("inf")); upper.append(float(budget_tenths))
    # Club cap.
    for team_id in candidates["team_id"].unique():
        constraints.append((candidates["team_id"] == team_id).astype(float).tolist())
        lower.append(0); upper.append(MAX_PER_CLUB)
    A = lil_matrix((len(constraints), n), dtype=float)
    for i, row in enumerate(constraints):
        for j, val in enumerate(row):
            if val:
                A[i, j] = val
    result = milp(
        c=objective,
        integrality=[1] * n,
        bounds=Bounds([0] * n, [1] * n),
        constraints=LinearConstraint(A.tocsr(), lower, upper),
        options={"time_limit": 20},
    )
    if not result.success or result.x is None:
        return None, f"Optimizer could not find a legal squad: {result.message}"
    chosen = candidates.loc[result.x > 0.5].copy()
    if not legal_squad(chosen, budget_tenths):
        return None, "Optimizer output failed a squad-rule validation check."
    return chosen, None


def best_starting_xi(squad: pd.DataFrame, projection_col: str):
    """Enumerate valid formations and select the XI with the highest projection."""
    gks = squad[squad["position"] == "GK"]
    defs = squad[squad["position"] == "DEF"]
    mids = squad[squad["position"] == "MID"]
    fwds = squad[squad["position"] == "FWD"]
    best = None
    best_score = -1.0
    # FPL legal formations: 3-5 DEF, 2-5 MID, 1-3 FWD; 1 GK; total 10 outfield.
    for d_count in range(3, 6):
        for m_count in range(2, 6):
            for f_count in range(1, 4):
                if d_count + m_count + f_count != 10:
                    continue
                if len(defs) < d_count or len(mids) < m_count or len(fwds) < f_count or gks.empty:
                    continue
                for _, gk in gks.iterrows():
                    for d_idx in combinations(defs.index, d_count):
                        for m_idx in combinations(mids.index, m_count):
                            for f_idx in combinations(fwds.index, f_count):
                                idx = [gk.name] + list(d_idx) + list(m_idx) + list(f_idx)
                                score = float(squad.loc[idx, projection_col].sum())
                                if score > best_score:
                                    best_score = score
                                    best = squad.loc[idx].copy()
    return (best, round(best_score, 2)) if best is not None else (None, 0.0)


def rank_captains(xi: pd.DataFrame, projection_col: str):
    return xi.sort_values(projection_col, ascending=False).head(3).copy()


def ensure_player_ids(frame: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with numeric FPL player IDs, recovering by name if needed."""
    out = frame.copy()
    if "id" in out.columns:
        out["id"] = pd.to_numeric(out["id"], errors="coerce")
    else:
        out["id"] = pd.NA
    if out["id"].isna().any() and "name" in out.columns and "id" in players.columns:
        name_to_id = players.drop_duplicates("name").set_index("name")["id"].to_dict()
        missing = out["id"].isna()
        out.loc[missing, "id"] = out.loc[missing, "name"].map(name_to_id)
    out = out[out["id"].notna()].copy()
    out["id"] = out["id"].astype(int)
    return out


def transfer_shortlist(squad, players, budget_tenths, free_transfers, hit_cost, projection_col, max_results=15):
    """Enumerate legal one-for-one swaps; show projected gain and hit-aware gain."""
    if squad.empty:
        return pd.DataFrame()
    squad = ensure_player_ids(squad, players)
    players = ensure_player_ids(players, players)
    if squad.empty or "id" not in squad.columns:
        return pd.DataFrame()
    current_ids = set(squad["id"].astype(int))
    current_score = float(squad[projection_col].sum())
    current_cost = int(squad["price"].sum())
    # Candidate replacement pool limited to best projections by position to keep calculations quick.
    pool = players[~players["id"].isin(current_ids)].copy()
    pool = pool.sort_values(projection_col, ascending=False).groupby("position", group_keys=False).head(80)
    rows = []
    for _, out in squad.iterrows():
        pos_pool = pool[pool["position"] == out["position"]]
        selling = int(out.get("selling_price", out["price"]))
        for _, incoming in pos_pool.iterrows():
            net = int(incoming["price"]) - selling
            if net > budget_tenths:
                continue
            trial = squad[squad["id"] != out["id"]].copy()
            newrow = incoming.to_dict()
            newrow["selling_price"] = int(incoming["price"])
            trial = pd.concat([trial, pd.DataFrame([newrow])], ignore_index=True)
            if not legal_squad(trial, current_cost - int(out["price"]) + int(incoming["price"])):
                continue
            gain = float(incoming[projection_col]) - float(out[projection_col])
            hit = 0 if free_transfers >= 1 else hit_cost
            rows.append({
                "Out": out["name"], "Out club": out["team"], "Pos": out["position"],
                "In": incoming["name"], "In club": incoming["team"],
                "Buy price": money(incoming["price"]), "Sell price": money(selling),
                "Net spend": money(max(0, net)), "Bank change": money(-net),
                "Projection gain": round(gain, 2), "Hit cost": hit,
                "Gain after hit": round(gain - hit, 2),
                "New fixtures": incoming.get("fixture_run", ""),
                "_gain": gain, "_out_id": int(out["id"]), "_in_id": int(incoming["id"]),
            })
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).sort_values(["Gain after hit", "Projection gain"], ascending=False)
    # Avoid proposing multiple alternatives for the same outgoing player.
    result = result.drop_duplicates(subset=["Out"], keep="first").head(max_results)
    return result.drop(columns=["_gain", "_out_id", "_in_id"], errors="ignore")


def chip_assessment(squad, optimized, fixtures, teams, start_gw, horizon, projection_col):
    messages = []
    if squad.empty:
        return messages
    current_xi, current_xi_score = best_starting_xi(squad, projection_col)
    if current_xi is not None:
        messages.append(("Current XI", current_xi_score))
    if optimized is not None:
        freehit_xi, freehit_xi_score = best_starting_xi(optimized, projection_col)
        if freehit_xi is not None:
            gain = round(freehit_xi_score - current_xi_score, 2)
            messages.append(("Optimized Free Hit XI", freehit_xi_score))
            messages.append(("Free Hit XI gain vs current XI", gain))
            if gain >= max(8.0, horizon * 2.5):
                messages.append(("Free Hit signal", "Potentially worth considering; inspect team news and the post-GW squad return."))
            elif gain >= max(4.0, horizon * 1.5):
                messages.append(("Free Hit signal", "Borderline. Prefer saving it unless your current XI has multiple availability/fixture problems."))
            else:
                messages.append(("Free Hit signal", "Likely save the chip; the optimized XI does not project far enough ahead of your current XI."))
    bench = squad[squad["pick_position"] > 11] if "pick_position" in squad else squad.iloc[11:]
    if not bench.empty:
        bench_points = round(float(bench[projection_col].sum()), 2)
        messages.append(("Bench projection (Bench Boost context)", bench_points))
    if current_xi is not None and not current_xi.empty:
        cap = current_xi.sort_values(projection_col, ascending=False).iloc[0]
        messages.append(("Captain suggestion", f'{cap["name"]} ({cap["team"]}), projection {cap[projection_col]:.2f}'))
        second = current_xi.sort_values(projection_col, ascending=False).iloc[1]
        messages.append(("Vice-captain suggestion", f'{second["name"]} ({second["team"]}), projection {second[projection_col]:.2f}'))
    messages.append(("Triple Captain context", "Use only if your top captain has a strong expected score, high minutes security, and ideally a double gameweek."))
    messages.append(("Wildcard context", "Best when squad issues persist over several weeks, not merely for a one-week fixture swing."))
    return messages


# ----------------------------- UI -------------------------------------------

st.title("⚽ FPL Team Advisor")
st.caption("Live public FPL data · current squad · transfer, captain and chip recommendations")

with st.sidebar:
    st.header("Manager & planning")
    manager_id_text = st.text_input("FPL Team ID", value="533979", key="manager_id_text")
    st.caption("Team ID 533979 is loaded by default. No password is requested.")
    free_transfers = 1
    hit_cost = 4
    st.caption("Transfer estimates assume 1 free transfer and a 4-point hit per additional transfer. The public API does not reliably expose the live free-transfer count.")
    horizon = st.slider("Projection horizon (gameweeks)", 1, 8, 3)

try:
    with st.spinner("Loading live FPL player, fixture and gameweek data…"):
        bootstrap, fixtures, team_map, raw_players = load_core_data()
    events = bootstrap.get("events", [])
    upcoming_events = event_options(events)
    if not upcoming_events:
        st.error("No upcoming gameweeks were returned by the FPL API.")
        st.stop()

    default_gw = default_event_id(events)
    gw_ids = [int(e["id"]) for e in upcoming_events]
    default_index = gw_ids.index(default_gw) if default_gw in gw_ids else 0

    top_cols = st.columns([1.2, 1, 1])
    with top_cols[0]:
        selected_gw = st.selectbox("Gameweek to assess", gw_ids, index=default_index)
    with top_cols[1]:
        st.metric("Players in API", len(raw_players))
    with top_cols[2]:
        last_updated = st.caption("API data cached for 15 minutes")

    projected = add_projections(raw_players, fixtures, selected_gw, int(horizon), team_map)
    projection_col = "xP_horizon" if horizon > 1 else "xP_next"

    profile = history = transfers = picks_payload = None
    squad = pd.DataFrame()
    loaded_pick_gw = None
    bank_api = None
    chips = []
    if manager_id_text.strip():
        try:
            manager_id = int(manager_id_text.strip())
            with st.spinner("Loading manager profile, history, transfers and picks…"):
                profile, history, transfers = load_manager(manager_id)
                picks_payload, loaded_pick_gw = get_team_picks(manager_id, selected_gw, events)
                squad = build_squad(picks_payload, projected)
                bank_api = infer_bank_tenths(profile, history, picks_payload)
                chips = used_chips(history)
            if not squad.empty:
                st.success(f"Loaded {profile.get('name', 'FPL manager')} — {len(squad)} players. Picks retrieved from GW{loaded_pick_gw}.")
            else:
                st.warning("No squad picks returned. Try another gameweek or confirm the Team ID.")
        except ValueError:
            st.error("Team ID must be a number.")
        except requests.HTTPError as exc:
            st.error(f"FPL API HTTP error: {exc}. The public endpoint may be temporarily unavailable or the ID may be incorrect.")
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            st.error(f"Could not load team data: {exc}")

    # Simple single-page dashboard: current squad + actionable recommendations.
    st.divider()
    if squad.empty:
        st.info("Your FPL squad could not be loaded. Check that team ID 533979 is correct, public, and the FPL API is available.")
    else:
        bank_tenths = bank_api
        if bank_tenths is not None:
            st.caption(f"Loaded {profile.get('name', 'your team')} · picks from GW{loaded_pick_gw} · Bank: {money(bank_tenths)}")
        else:
            st.caption(f"Loaded {profile.get('name', 'your team')} · picks from GW{loaded_pick_gw} · Bank balance unavailable from API")

        a, b, c, d = st.columns(4)
        a.metric("Squad players", len(squad))
        b.metric("Bank", money(bank_tenths) if bank_tenths is not None else "Unavailable")
        c.metric("Squad value", money(int(squad["price"].sum()) + (bank_tenths or 0)))
        d.metric("Projected points", round(float(squad[projection_col].sum()), 1))

        st.subheader("Current squad")
        st.caption("Fixture cells are color-coded by FDR: dark green = easiest (1), grey-white = average (3), dark red = hardest (5).")
        team_view = squad.copy()
        team_view["Price"] = team_view["price"].map(money)
        team_view["Sell price"] = team_view["selling_price"].map(money)
        # One fixture column per gameweek keeps the FDR colors inside the squad table.
        fixture_gws = list(range(int(selected_gw), int(selected_gw) + int(horizon)))
        for gw in fixture_gws:
            labels, ratings = [], []
            for _, player in team_view.iterrows():
                fs = [fx for fx in fixtures_for_team(fixtures, int(player["team_id"]), gw, 1, team_map) if fx["gw"] == gw]
                if not fs:
                    labels.append("—")
                    ratings.append(None)
                else:
                    labels.append(" / ".join(f"{fx['opponent']} ({fx['venue']}) · {fx['fdr']}" for fx in fs))
                    ratings.append(round(sum(fx["fdr"] for fx in fs) / len(fs)))
            team_view[f"GW{gw} fixture"] = labels
        show_cols = ["pick_position", "name", "position", "team", "Price", "Sell price", "xP_next", "availability"] + [f"GW{gw} fixture" for gw in fixture_gws] + ["is_captain", "is_vice_captain"]
        show_cols = [col for col in show_cols if col in team_view.columns]
        display = team_view[show_cols].rename(columns={
            "pick_position":"Pick", "name":"Player", "position":"Pos", "team":"Club",
            "xP_next":"xP next GW", "availability":"Availability",
            "is_captain":"C", "is_vice_captain":"VC"
        })
        def fixture_cell_style(value):
            import re
            found = re.findall(r"·\s*([1-5])", str(value))
            if not found:
                return ""
            rating = round(sum(int(x) for x in found) / len(found))
            return style_fdr(rating)
        fixture_cols = [f"GW{gw} fixture" for gw in fixture_gws if f"GW{gw} fixture" in display.columns]
        st.dataframe(display.style.map(fixture_cell_style, subset=fixture_cols), use_container_width=True, hide_index=True)

        # Recommendations are consolidated here; no separate optimizer/chip/player tabs.
        st.subheader("Recommendations")
        xi, xi_score = best_starting_xi(squad, projection_col)
        if xi is not None and not xi.empty:
            captains = rank_captains(xi, projection_col)
            best = captains.iloc[0]
            vice = captains.iloc[1] if len(captains) > 1 else captains.iloc[0]
            cap1, cap2 = st.columns(2)
            cap1.metric("Captain", f"{best['name']} · {best['team']}", f"{float(best[projection_col]):.2f} projected pts")
            cap2.metric("Vice-captain", f"{vice['name']} · {vice['team']}", f"{float(vice[projection_col]):.2f} projected pts")
            with st.expander("Why these captain picks?"):
                st.dataframe(captains[["name", "team", "position", projection_col, "availability", "fixture_run"]].rename(columns={
                    "name":"Player", "team":"Club", "position":"Pos", projection_col:"Projection", "availability":"Availability", "fixture_run":"Fixture run"
                }), use_container_width=True, hide_index=True)

        st.markdown("**Transfer recommendations**")
        if bank_tenths is None:
            st.warning("Transfer shortlist needs your bank balance, which the public API did not return. Check the bank in FPL and try again later; the app won't guess it.")
        elif st.button("Get transfer recommendations", type="primary"):
            with st.spinner("Checking legal one-for-one transfers against your budget…"):
                recs = transfer_shortlist(squad, projected, int(bank_tenths), int(free_transfers), int(hit_cost), projection_col)
                st.session_state["transfer_recs"] = recs
        recs = st.session_state.get("transfer_recs")
        if recs is not None:
            if recs.empty:
                st.info("No legal one-for-one transfer with a positive candidate gain was found in the current search.")
            else:
                useful = recs[recs["Gain after hit"] > 0].copy()
                if useful.empty:
                    st.info("No shortlisted transfer has a positive projected gain after the assumed hit cost.")
                else:
                    st.dataframe(useful.head(8), use_container_width=True, hide_index=True)
                    st.caption("Heuristic estimates, not guarantees. Assumes one free transfer; verify your actual free transfers, selling prices, injuries and press conferences before the deadline.")

        st.markdown("**Chip recommendation**")
        used = {str(ch.get("name", "")).lower() for ch in chips if isinstance(ch, dict)}
        if any("freehit" in x for x in used):
            freehit_status = "Free Hit appears used already; verify in the official FPL app."
        else:
            freehit_status = "Save Free Hit unless your starting XI has several unavailable players or a major blank-gameweek problem."
        if any("bboost" in x for x in used):
            bench_status = "Bench Boost appears used already; verify in the official FPL app."
        else:
            bench = squad[squad["pick_position"] > 11] if "pick_position" in squad else squad.iloc[11:]
            bench_score = float(bench[projection_col].sum()) if not bench.empty else 0.0
            bench_status = (f"Bench projection is {bench_score:.1f}; consider Bench Boost only when all four bench players are likely to play, ideally in a double gameweek." if bench_score >= 12 else f"Bench projection is {bench_score:.1f}; likely save Bench Boost for a stronger bench / double gameweek.")
        if any("wildcard" in x for x in used):
            wildcard_status = "Wildcard appears used already; verify in the official FPL app."
        else:
            flagged_count = int((squad["availability"] != "Likely available").sum())
            wildcard_status = f"{flagged_count} squad player(s) have availability flags. Consider Wildcard only if several issues persist over multiple gameweeks, not just for one fixture swing."
        if any("3xc" in x for x in used):
            tc_status = "Triple Captain appears used already; verify in the official FPL app."
        else:
            tc_status = f"Captain is {best['name'] if xi is not None and not xi.empty else 'not available'}. Prefer Triple Captain in a double gameweek with high minutes security; otherwise save it."
        st.write(f"- **Free Hit:** {freehit_status}")
        st.write(f"- **Wildcard:** {wildcard_status}")
        st.write(f"- **Bench Boost:** {bench_status}")
        st.write(f"- **Triple Captain:** {tc_status}")

except requests.RequestException as exc:
    st.error(f"Could not load FPL data: {exc}. Please refresh and try again.")
