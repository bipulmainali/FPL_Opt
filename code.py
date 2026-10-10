
"""
FPL Team Advisor — Streamlit
Public FPL API, squad optimization, transfers and chip guidance.
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

st.set_page_config(
    page_title="FPL Team Advisor",
    page_icon="⚽",
    layout="wide",
)


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
    teams = {
        int(t["id"]): t
        for t in bootstrap.get("teams", [])
    }

    rows = []
    for p in bootstrap.get("elements", []):
        team_id = int(p.get("team", 0))
        rows.append({
            "id": int(p["id"]),
            "name": (
                p.get("web_name")
                or p.get("second_name")
                or f'Player {p["id"]}'
            ),
            "full_name": (
                f'{p.get("first_name", "")} '
                f'{p.get("second_name", "")}'
            ).strip(),
            "team_id": team_id,
            "team": teams.get(team_id, {}).get("name", "Unknown"),
            "position": POSITION_NAMES.get(
                int(p.get("element_type", 0)), "?"
            ),
            "price": int(p.get("now_cost", 0)),
            "form": safe_float(p.get("form")),
            "points": safe_float(p.get("total_points")),
            "ppg": safe_float(p.get("points_per_game")),
            "minutes": int(p.get("minutes") or 0),
            "selected_by": safe_float(p.get("selected_by_percent")),
            "status": p.get("status", ""),
            "chance": p.get("chance_of_playing_next_round"),
            "news": p.get("news") or "",
            "ict": safe_float(p.get("ict_index")),
            "xgi": safe_float(p.get("expected_goal_involvements")),
            "ep_next": safe_float(p.get("ep_next")),
            "ep_this": safe_float(p.get("ep_this")),
            "value": safe_float(p.get("value_season")),
            "transfers_in_event": int(
                p.get("transfers_in_event") or 0
            ),
            "transfers_out_event": int(
                p.get("transfers_out_event") or 0
            ),
            "cost_change_start": int(
                p.get("cost_change_start") or 0
            ),
            "cost_change_event": int(
                p.get("cost_change_event") or 0
            ),
            "starts": int(p.get("starts") or 0),
            "goals": int(p.get("goals_scored") or 0),
            "assists": int(p.get("assists") or 0),
            "clean_sheets": int(p.get("clean_sheets") or 0),
            "bonus": int(p.get("bonus") or 0),
            "yellow_cards": int(p.get("yellow_cards") or 0),
            "red_cards": int(p.get("red_cards") or 0),
            "penalties_order": p.get("penalties_order"),
            "corners_order": p.get(
                "corners_and_indirect_freekicks_order"
            ),
            "direct_fk_order": p.get("direct_freekicks_order"),
        })

    return (
        bootstrap,
        fixtures,
        teams,
        pd.DataFrame(rows),
    )


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

    return max([
        int(e.get("id", 1)) for e in events
    ] or [1])


def fixtures_for_team(
    fixtures,
    team_id: int,
    start_gw: int,
    horizon: int,
    teams,
):
    """Return upcoming fixtures, including multiple fixtures per GW."""
    out = []

    for f in fixtures:
        gw = f.get("event")

        if (
            gw is None
            or int(gw) < start_gw
            or int(gw) >= start_gw + horizon
        ):
            continue

        if int(f.get("team_h", -1)) == team_id:
            opponent_id = int(f["team_a"])
            out.append({
                "gw": int(gw),
                "opponent": teams.get(
                    opponent_id, {}
                ).get("name", "?"),
                "venue": "H",
                "fdr": int(f.get("team_h_difficulty") or 3),
                "started": bool(f.get("started")),
                "finished": bool(f.get("finished")),
            })

        elif int(f.get("team_a", -1)) == team_id:
            opponent_id = int(f["team_h"])
            out.append({
                "gw": int(gw),
                "opponent": teams.get(
                    opponent_id, {}
                ).get("name", "?"),
                "venue": "A",
                "fdr": int(f.get("team_a_difficulty") or 3),
                "started": bool(f.get("started")),
                "finished": bool(f.get("finished")),
            })

    return sorted(
        out,
        key=lambda x: (x["gw"], x["venue"]),
    )


def fixture_label(
    fixtures,
    team_id,
    start_gw,
    horizon,
    teams,
):
    fs = fixtures_for_team(
        fixtures, int(team_id), start_gw, horizon, teams
    )

    if not fs:
        return "No scheduled fixture"

    by_gw = {}

    for f in fs:
        by_gw.setdefault(f["gw"], []).append(
            f'{f["opponent"]} ({f["venue"]}, FDR {f["fdr"]})'
        )

    return " · ".join(
        f"GW{gw}: {', '.join(names)}"
        for gw, names in by_gw.items()
    )


def availability_multiplier(row) -> float:
    status = str(row.get("status", ""))
    chance = row.get("chance")

    if status in {"i", "s", "u", "n"}:
        return 0.10

    if chance is not None and not pd.isna(chance):
        try:
            return max(
                0.15,
                min(1.0, float(chance) / 100),
            )
        except (ValueError, TypeError):
            pass

    return 1.0


def availability_label(row):
    status = str(row.get("status", ""))
    chance = row.get("chance")

    if status in {"i", "s", "u", "n"}:
        return "Unavailable / flagged"

    if chance is not None and not pd.isna(chance):
        chance = safe_float(chance, 100)

        if chance < 75:
            return "Doubtful"

        if status == "d" or chance < 100:
            return "Monitor"

    return "Likely available"


def player_base_projection(row) -> float:
    """
    Blend FPL next-GW estimate, points per game and recent form.
    Reduce the result for low minutes and availability risks.
    """
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
    """Apply a modest fixture adjustment; neutral at FDR 3."""
    return max(
        0.78,
        min(1.22, 1.0 + (3 - int(fdr)) * 0.055),
    )


def projected_points(
    row,
    fixtures,
    start_gw,
    horizon,
    teams,
) -> float:
    base = player_base_projection(row)
    team_id = int(row.get("team_id", 0))

    fs = fixtures_for_team(
        fixtures, team_id, start_gw, horizon, teams
    )

    if not fs:
        return 0.0

    total = sum(
        base * fixture_multiplier(f["fdr"])
        for f in fs
    )

    return round(total, 2)


def add_projections(
    players,
    fixtures,
    start_gw,
    horizon,
    teams,
):
    df = players.copy()

    df["xP_next"] = df.apply(
        player_base_projection, axis=1
    ).round(2)

    df["xP_horizon"] = df.apply(
        lambda r: projected_points(
            r, fixtures, start_gw, horizon, teams
        ),
        axis=1,
    )

    df["fixture_run"] = df.apply(
        lambda r: fixture_label(
            fixtures, r["team_id"], start_gw, horizon, teams
        ),
        axis=1,
    )

    df["availability"] = df.apply(
        availability_label, axis=1
    )

    df["value_per_m"] = df.apply(
        lambda r: round(
            r["xP_horizon"] / max(0.1, r["price"] / 10),
            2,
        ),
        axis=1,
    )

    return df

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
    """Try selected GW, then the latest finished GW."""
    attempts = [selected_gw]
    finished = [
        int(e["id"]) for e in events if e.get("finished")
    ]
    attempts += sorted(
        [gw for gw in finished if gw != selected_gw],
        reverse=True,
    )

    errors = []

    for gw in attempts:
        try:
            payload = load_picks(manager_id, gw)
            if payload.get("picks"):
                return payload, gw
        except requests.RequestException as exc:
            errors.append(str(exc))

    raise RuntimeError(
        "Could not load public picks for this team/gameweek. "
        + " ".join(errors[-2:])
    )


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
        r["is_vice_captain"] = bool(
            pick.get("is_vice_captain")
        )
        r["purchase_price"] = int(
            pick.get("purchase_price", r["price"])
        )
        r["selling_price"] = int(
            pick.get("selling_price", r["price"])
        )
        r["is_starting"] = r["pick_position"] <= 11
        rows.append(r)

    return pd.DataFrame(rows)


def infer_bank_tenths(profile, history, picks_payload):
    entry_hist = picks_payload.get("entry_history") or {}

    if entry_hist.get("bank") is not None:
        return int(entry_hist["bank"])

    current = history.get("current") or []

    if current and current[-1].get("bank") is not None:
        return int(current[-1]["bank"])

    return 0


def used_chips(history):
    return history.get("chips") or []


def legal_squad(df: pd.DataFrame, budget_tenths: int) -> bool:
    if len(df) != 15 or df["id"].nunique() != 15:
        return False

    if int(df["price"].sum()) > budget_tenths:
        return False

    for pos, count in POSITION_QUOTAS.items():
        if int((df["position"] == pos).sum()) != count:
            return False

    return bool(
        (df.groupby("team_id").size() <= MAX_PER_CLUB).all()
    )


def optimize_squad_milp(
    players: pd.DataFrame,
    budget_tenths: int,
    projection_col: str,
):
    """
    Maximize projected points using binary integer programming.
    Enforces squad size, position quotas, budget and club limit.
    """
    if not SCIPY_AVAILABLE:
        return None, "Install scipy to enable the full optimizer."

    candidates = players.copy().reset_index(drop=True)
    n = len(candidates)

    if n < 15:
        return None, "Not enough player records to optimize."

    objective = -candidates[projection_col].astype(float).to_numpy()

    constraints = []
    lower = []
    upper = []

    # Exactly 15 players.
    constraints.append([1.0] * n)
    lower.append(15)
    upper.append(15)

    # Required positional composition.
    for pos, count in POSITION_QUOTAS.items():
        constraints.append(
            (candidates["position"] == pos).astype(float).tolist()
        )
        lower.append(count)
        upper.append(count)

    # Total squad budget.
    constraints.append(
        candidates["price"].astype(float).tolist()
    )
    lower.append(-float("inf"))
    upper.append(float(budget_tenths))

    # No more than three players from any one club.
    for team_id in candidates["team_id"].unique():
        constraints.append(
            (candidates["team_id"] == team_id)
            .astype(float)
            .tolist()
        )
        lower.append(0)
        upper.append(MAX_PER_CLUB)

    A = lil_matrix((len(constraints), n), dtype=float)

    for i, row in enumerate(constraints):
        for j, val in enumerate(row):
            if val:
                A[i, j] = val

    result = milp(
        c=objective,
        integrality=[1] * n,
        bounds=Bounds([0] * n, [1] * n),
        constraints=LinearConstraint(
            A.tocsr(), lower, upper
        ),
        options={"time_limit": 20},
    )

    if not result.success or result.x is None:
        return None, (
            f"Optimizer could not find a legal squad: "
            f"{result.message}"
        )

    chosen = candidates.loc[result.x > 0.5].copy()

    if not legal_squad(chosen, budget_tenths):
        return None, "Optimizer output failed squad validation."

    return chosen, None


def best_starting_xi(
    squad: pd.DataFrame,
    projection_col: str,
):
    """Find the highest-projection legal starting XI."""
    gks = squad[squad["position"] == "GK"]
    defs = squad[squad["position"] == "DEF"]
    mids = squad[squad["position"] == "MID"]
    fwds = squad[squad["position"] == "FWD"]

    best = None
    best_score = -1.0

    for d_count in range(3, 6):
        for m_count in range(2, 6):
            for f_count in range(1, 4):
                if d_count + m_count + f_count != 10:
                    continue

                if (
                    len(defs) < d_count
                    or len(mids) < m_count
                    or len(fwds) < f_count
                    or gks.empty
                ):
                    continue

                for _, gk in gks.iterrows():
                    for d_idx in combinations(defs.index, d_count):
                        for m_idx in combinations(mids.index, m_count):
                            for f_idx in combinations(fwds.index, f_count):
                                idx = (
                                    [gk.name]
                                    + list(d_idx)
                                    + list(m_idx)
                                    + list(f_idx)
                                )

                                score = float(
                                    squad.loc[idx, projection_col].sum()
                                )

                                if score > best_score:
                                    best_score = score
                                    best = squad.loc[idx].copy()

    if best is None:
        return None, 0.0

    return best, round(best_score, 2)


def rank_captains(xi: pd.DataFrame, projection_col: str):
    return xi.sort_values(
        projection_col, ascending=False
    ).head(3).copy()


def transfer_shortlist(
    squad,
    players,
    budget_tenths,
    free_transfers,
    hit_cost,
    projection_col,
    max_results=15,
):
    """Compare legal one-for-one transfers using projected score gains."""
    if squad.empty:
        return pd.DataFrame()

    current_ids = set(int(x) for x in squad["id"])
    current_cost = int(squad["price"].sum())

    pool = players[~players["id"].isin(current_ids)].copy()
    pool = (
        pool.sort_values(projection_col, ascending=False)
        .groupby("position", group_keys=False)
        .head(80)
    )

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

            trial = pd.concat(
                [trial, pd.DataFrame([newrow])],
                ignore_index=True,
            )

            trial_cost = (
                current_cost
                - int(out["price"])
                + int(incoming["price"])
            )

            if not legal_squad(trial, trial_cost):
                continue

            gain = (
                float(incoming[projection_col])
                - float(out[projection_col])
            )

            hit = 0 if free_transfers >= 1 else hit_cost

            rows.append({
                "Out": out["name"],
                "Out club": out["team"],
                "Pos": out["position"],
                "In": incoming["name"],
                "In club": incoming["team"],
                "Buy price": money(incoming["price"]),
                "Sell price": money(selling),
                "Net spend": money(max(0, net)),
                "Bank change": money(-net),
                "Projection gain": round(gain, 2),
                "Hit cost": hit,
                "Gain after hit": round(gain - hit, 2),
                "New fixtures": incoming.get("fixture_run", ""),
                "_out_id": int(out["id"]),
                "_in_id": int(incoming["id"]),
            })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).sort_values(
        ["Gain after hit", "Projection gain"],
        ascending=False,
    )

    result = result.drop_duplicates(
        subset=["Out"], keep="first"
    ).head(max_results)

    return result.drop(
        columns=["_out_id", "_in_id"],
        errors="ignore",
    )

def chip_assessment(
    squad,
    optimized,
    fixtures,
    teams,
    start_gw,
    horizon,
    projection_col,
):
    messages = []

    if squad.empty:
        return messages

    current_xi, current_xi_score = best_starting_xi(
        squad, projection_col
    )

    if current_xi is not None:
        messages.append(("Current XI", current_xi_score))

    if optimized is not None:
        freehit_xi, freehit_xi_score = best_starting_xi(
            optimized, projection_col
        )

        if freehit_xi is not None:
            gain = round(
                freehit_xi_score - current_xi_score, 2
            )

            messages.append(
                ("Optimized Free Hit XI", freehit_xi_score)
            )
            messages.append(
                ("Free Hit XI gain vs current XI", gain)
            )

            if gain >= max(8.0, horizon * 2.5):
                messages.append((
                    "Free Hit signal",
                    "Potentially worth considering. Check team news "
                    "and the squad you will retain after the gameweek."
                ))
            elif gain >= max(4.0, horizon * 1.5):
                messages.append((
                    "Free Hit signal",
                    "Borderline. Consider saving the chip unless "
                    "your current XI has multiple fixture or "
                    "availability problems."
                ))
            else:
                messages.append((
                    "Free Hit signal",
                    "Likely save the chip. The optimized XI does not "
                    "project far enough ahead of your current XI."
                ))

    if "pick_position" in squad:
        bench = squad[squad["pick_position"] > 11]
    else:
        bench = squad.iloc[11:]

    if not bench.empty:
        bench_points = round(
            float(bench[projection_col].sum()), 2
        )
        messages.append((
            "Bench projection (Bench Boost context)",
            bench_points
        ))

    if current_xi is not None and not current_xi.empty:
        ranked = current_xi.sort_values(
            projection_col, ascending=False
        )

        captain = ranked.iloc[0]
        messages.append((
            "Captain suggestion",
            f'{captain["name"]} ({captain["team"]}), '
            f'projection {captain[projection_col]:.2f}'
        ))

        if len(ranked) > 1:
            vice = ranked.iloc[1]
            messages.append((
                "Vice-captain suggestion",
                f'{vice["name"]} ({vice["team"]}), '
                f'projection {vice[projection_col]:.2f}'
            ))

    messages.append((
        "Triple Captain context",
        "Consider a reliable high-projection captain with secure "
        "minutes, ideally during a double gameweek."
    ))

    messages.append((
        "Wildcard context",
        "Best considered when several positions need improvement "
        "over multiple gameweeks, rather than for a single fixture."
    ))

    messages.append((
        "Bench Boost context",
        "Compare all 15 players' expected minutes, availability "
        "and fixture schedules before activating the chip."
    ))

    return messages


# ----------------------------- UI -------------------------------------------

st.title("⚽ FPL Team Advisor")
st.caption(
    "Live public FPL data · squad optimization · "
    "transfer planning · chip decision support"
)

with st.sidebar:
    st.header("Manager & planning")

    manager_id_text = st.text_input(
        "FPL Team ID",
        placeholder="e.g. 123456",
    )

    st.caption(
        "The numeric ID in your public FPL team URL. "
        "No password is requested."
    )

    bank_override = st.number_input(
        "Bank balance (£m)",
        min_value=0.0,
        max_value=30.0,
        value=0.0,
        step=0.1,
    )

    use_api_bank = st.checkbox(
        "Use bank balance from API when available",
        value=True,
    )

    free_transfers = st.number_input(
        "Free transfers available",
        min_value=1,
        max_value=5,
        value=1,
        step=1,
    )

    hit_cost = st.number_input(
        "Points hit per extra transfer",
        min_value=0,
        max_value=12,
        value=4,
        step=1,
    )

    horizon = st.slider(
        "Projection horizon (gameweeks)",
        min_value=1,
        max_value=8,
        value=3,
    )

    st.markdown("---")
    st.caption(
        "If the API cannot return bank or selling prices, "
        "use the manual inputs below."
    )


try:
    with st.spinner(
        "Loading live FPL player, fixture and gameweek data…"
    ):
        bootstrap, fixtures, team_map, raw_players = (
            load_core_data()
        )

    events = bootstrap.get("events", [])
    upcoming_events = event_options(events)

    if not upcoming_events:
        st.error(
            "No upcoming gameweeks were returned by the FPL API."
        )
        st.stop()

    default_gw = default_event_id(events)
    gw_ids = [int(e["id"]) for e in upcoming_events]

    default_index = (
        gw_ids.index(default_gw)
        if default_gw in gw_ids
        else 0
    )

    top_cols = st.columns([1.2, 1, 1])

    with top_cols[0]:
        selected_gw = st.selectbox(
            "Gameweek to assess",
            gw_ids,
            index=default_index,
        )

    with top_cols[1]:
        st.metric("Players in API", len(raw_players))

    with top_cols[2]:
        st.caption("API data cached for 15 minutes")

    projected = add_projections(
        raw_players,
        fixtures,
        selected_gw,
        int(horizon),
        team_map,
    )

    projection_col = (
        "xP_horizon" if horizon > 1 else "xP_next"
    )

    profile = None
    history = None
    transfers = None
    picks_payload = None
    squad = pd.DataFrame()
    loaded_pick_gw = None
    bank_api = None
    chips = []

    if manager_id_text.strip():
        try:
            manager_id = int(manager_id_text.strip())

            with st.spinner(
                "Loading manager profile, history, transfers and picks…"
            ):
                profile, history, transfers = load_manager(
                    manager_id
                )

                picks_payload, loaded_pick_gw = get_team_picks(
                    manager_id, selected_gw, events
                )

                squad = build_squad(
                    picks_payload, projected
                )

                bank_api = infer_bank_tenths(
                    profile, history, picks_payload
                )

                chips = used_chips(history)

            if not squad.empty:
                st.success(
                    f'Loaded {profile.get("name", "FPL manager")} '
                    f'— {len(squad)} players. '
                    f'Picks retrieved from GW{loaded_pick_gw}.'
                )
            else:
                st.warning(
                    "No squad picks returned. Try another "
                    "gameweek or confirm the Team ID."
                )

        except ValueError:
            st.error("Team ID must be a number.")

        except requests.HTTPError as exc:
            st.error(
                f"FPL API HTTP error: {exc}. The public endpoint "
                "may be unavailable or the ID may be incorrect."
            )

        except (
            requests.RequestException,
            RuntimeError,
            KeyError,
        ) as exc:
            st.error(f"Could not load team data: {exc}")

    (
        tab_team,
        tab_optimizer,
        tab_transfers,
        tab_chips,
        tab_players,
        tab_history,
    ) = st.tabs([
        "My Team",
        "Squad Optimizer",
        "Transfers",
        "Chips",
        "Player Browser",
        "History",
    ])

    with tab_team:
        if squad.empty:
            st.info(
                "Enter your FPL Team ID in the sidebar to load "
                "your squad. The player browser and squad "
                "optimizer can still be used without it."
            )

        else:
            bank_tenths = (
                bank_api
                if use_api_bank and bank_api is not None
                else money_input_to_tenths(bank_override)
            )

            if bank_api is not None and use_api_bank:
                st.caption(
                    "Bank balance inferred from public API: "
                    f"{money(bank_tenths)}. Confirm against "
                    "the official app if it differs."
                )
            else:
                st.caption(
                    "Using manually entered bank balance: "
                    f"{money(bank_tenths)}."
                )

            squad_cost = int(squad["price"].sum())
            squad_value = squad_cost + bank_tenths

            a, b, c, d = st.columns(4)

            a.metric("Squad market cost", money(squad_cost))
            b.metric("Bank", money(bank_tenths))
            c.metric("Squad + bank", money(squad_value))

            d.metric(
                "Team value (API)",
                money(profile.get("last_deadline_value", 0))
                if profile
                else "—",
            )

            st.subheader("Current squad")

            team_view = squad.copy()
            team_view["price_display"] = (
                team_view["price"].map(money)
            )
            team_view["sell_display"] = (
                team_view["selling_price"].map(money)
            )

            cols = [
                "pick_position",
                "name",
                "position",
                "team",
                "price_display",
                "sell_display",
                "xP_next",
                "xP_horizon",
                "availability",
                "fixture_run",
                "is_captain",
                "is_vice_captain",
            ]

            cols = [
                c for c in cols if c in team_view.columns
            ]

            team_view = team_view[cols].rename(columns={
                "pick_position": "Pick",
                "name": "Player",
                "position": "Pos",
                "team": "Club",
                "price_display": "Price",
                "sell_display": "Sell price",
                "xP_next": "xP next GW",
                "xP_horizon": "xP horizon",
                "availability": "Availability",
                "fixture_run": "Fixtures",
                "is_captain": "Captain",
                "is_vice_captain": "Vice-captain",
            })

            st.dataframe(
                team_view,
                use_container_width=True,
                hide_index=True,
            )

            if len(squad) != 15:
                st.warning(
                    f"Expected 15 picks, found {len(squad)}. "
                    "Optimization and chip comparisons may "
                    "be incomplete."
                )

            st.subheader("Team structure and risk")

            risk_cols = st.columns(4)

            risk_cols[0].metric(
                "Flagged / doubtful",
                int(
                    squad["availability"].isin([
                        "Unavailable / flagged",
                        "Doubtful",
                        "Monitor",
                    ]).sum()
                ),
            )

            risk_cols[1].metric(
                "Horizon projection",
                round(float(squad["xP_horizon"].sum()), 1),
            )

            risk_cols[2].metric(
                "Starters projection",
                round(
                    float(
                        squad.loc[
                            squad["pick_position"] <= 11,
                            "xP_horizon",
                        ].sum()
                    ),
                    1,
                ),
            )

            risk_cols[3].metric(
                "Bench projection",
                round(
                    float(
                        squad.loc[
                            squad["pick_position"] > 11,
                            "xP_horizon",
                        ].sum()
                    ),
                    1,
                ),
            )

            with st.expander("Availability news"):
                flagged = squad[
                    squad["availability"] != "Likely available"
                ]

                if flagged.empty:
                    st.write(
                        "No availability flags detected in API data."
                    )
                else:
                    st.dataframe(
                        flagged[[
                            "name",
                            "team",
                            "availability",
                            "chance",
                            "news",
                        ]],
                        use_container_width=True,
                        hide_index=True,
                    )

    with tab_optimizer:
        st.subheader("Optimize a legal 15-player squad")

        if not SCIPY_AVAILABLE:
            st.error(
                "The full optimizer requires scipy. "
                "Install it with: pip install scipy"
            )

        budget_default = 100.0

        if not squad.empty:
            inferred = (
                int(squad["price"].sum())
                + (
                    bank_api
                    or money_input_to_tenths(bank_override)
                )
            ) / 10

            budget_default = max(
                80.0, min(120.0, inferred)
            )

        budget = st.number_input(
            "Total squad budget (£m)",
            min_value=80.0,
            max_value=120.0,
            value=float(budget_default),
            step=0.1,
        )

        optimizer_scope = st.radio(
            "Optimize for",
            [
                "Next gameweek",
                f"Next {horizon} gameweeks",
            ],
            horizontal=True,
        )

        opt_col = (
            "xP_next"
            if optimizer_scope == "Next gameweek"
            else "xP_horizon"
        )

        if st.button(
            "Find best legal squad",
            type="primary",
        ):
            with st.spinner(
                "Optimizing squad under FPL rules…"
            ):
                opt_squad, opt_error = optimize_squad_milp(
                    projected,
                    money_input_to_tenths(budget),
                    opt_col,
                )

            if opt_error:
                st.error(opt_error)
            else:
                st.session_state["optimized_squad"] = opt_squad
                st.session_state[
                    "optimized_projection_col"
                ] = opt_col

        opt_squad = st.session_state.get(
            "optimized_squad"
        )

        opt_col_saved = st.session_state.get(
            "optimized_projection_col", opt_col
        )

        if opt_squad is not None:
            st.success(
                "Legal squad found · projected score proxy "
                f"{opt_squad[opt_col_saved].sum():.1f} "
                "over the selected horizon."
            )

            opt_show = opt_squad.copy()
            opt_show["Price"] = opt_show["price"].map(money)

            st.dataframe(
                opt_show[[
                    "name",
                    "position",
                    "team",
                    "Price",
                    opt_col_saved,
                    "availability",
                    "fixture_run",
                ]].rename(columns={
                    "name": "Player",
                    "position": "Pos",
                    "team": "Club",
                    opt_col_saved: "Projection",
                    "availability": "Availability",
                    "fixture_run": "Fixtures",
                }),
                use_container_width=True,
                hide_index=True,
            )

            xi, xi_score = best_starting_xi(
                opt_squad, opt_col_saved
            )

            if xi is not None:
                st.markdown(
                    "#### Best starting XI and captain"
                )

                xi = xi.sort_values(
                    "position",
                    key=lambda s: s.map({
                        "GK": 0,
                        "DEF": 1,
                        "MID": 2,
                        "FWD": 3,
                    }),
                )

                st.dataframe(
                    xi[[
                        "name",
                        "position",
                        "team",
                        opt_col_saved,
                        "availability",
                    ]].rename(columns={
                        "name": "Player",
                        "position": "Pos",
                        "team": "Club",
                        opt_col_saved: "Projection",
                        "availability": "Availability",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

                cap = rank_captains(xi, opt_col_saved)

                st.write(
                    "Captain candidates: "
                    + " · ".join(
                        f'{r["name"]} '
                        f'({r[opt_col_saved]:.2f})'
                        for _, r in cap.iterrows()
                    )
                )

            if not squad.empty:
                current_xi, current_score = best_starting_xi(
                    squad, opt_col_saved
                )

                if current_xi is not None and xi is not None:
                    st.metric(
                        "Optimized XI gain vs current XI",
                        f"{xi_score - current_score:+.2f}",
                    )

            if not squad.empty:
                current_xi, current_score = best_starting_xi(squad, opt_col_saved)
                if current_xi is not None and xi is not None:
                    st.metric("Optimized XI advantage vs current XI", f"{xi_score - current_score:+.2f} projection points")
        else:
            st.caption("The optimizer uses binary integer programming and enforces 15 players, position quotas, budget and a maximum of three players per club.")

    with tab_transfers:
        st.subheader("Transfer recommendations")
        if squad.empty:
            st.info("Load your team to compare transfers against your current squad.")
        else:
            bank_tenths = bank_api if use_api_bank and bank_api is not None else money_input_to_tenths(bank_override)
            if st.button("Calculate transfer shortlist"):
                recs = transfer_shortlist(
                    squad, projected, bank_tenths, int(free_transfers), int(hit_cost), projection_col
                )
                st.session_state["transfer_recs"] = recs
            recs = st.session_state.get("transfer_recs")
            if recs is not None:
                if recs.empty:
                    st.warning("No legal one-for-one transfer candidates found within the current budget.")
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
            chip_choice = st.selectbox(
                "Chip", ["Free Hit", "Wildcard", "Bench Boost", "Triple Captain"]
            )
            used_names = [
                str(c.get("name", "")).lower()
                for c in chips if isinstance(c, dict)
            ]
            chip_alias = {
                "Free Hit": "freehit",
                "Wildcard": "wildcard",
                "Bench Boost": "bboost",
                "Triple Captain": "3xc",
            }
            is_used = any(chip_alias[chip_choice] in x for x in used_names)

            if is_used:
                st.warning(
                    f"The public history suggests {chip_choice} has already been used. "
                    "Confirm chip rules for this season."
                )
            else:
                if chip_choice == "Free Hit":
                    st.write(
                        "Compare the best optimized XI with your current XI. Free Hit "
                        "is most attractive when the one-week gain is substantial and "
                        "the squad returns to normal afterward."
                    )
                elif chip_choice == "Wildcard":
                    st.write(
                        "Use when multiple squad problems persist over several gameweeks. "
                        "Compare the optimized multiweek squad against a small number "
                        "of transfers and account for future flexibility."
                    )
                elif chip_choice == "Bench Boost":
                    bench = (
                        squad[squad["pick_position"] > 11]
                        if "pick_position" in squad
                        else squad.iloc[11:]
                    )
                    st.metric(
                        "Current bench projection",
                        round(float(bench[projection_col].sum()), 2)
                    )
                    st.write(
                        "Bench Boost is most attractive when all four bench players "
                        "have strong minutes security and good fixtures, particularly "
                        "in a double gameweek."
                    )
                else:
                    xi, _ = best_starting_xi(squad, projection_col)
                    if xi is not None:
                        caps = rank_captains(xi, projection_col)
                        st.dataframe(
                            caps[[
                                "name", "team", projection_col,
                                "availability", "fixture_run"
                            ]].rename(columns={
                                "name": "Captain candidate",
                                "team": "Club",
                                projection_col: "Projection",
                                "availability": "Availability",
                                "fixture_run": "Fixtures",
                            }),
                            use_container_width=True,
                            hide_index=True,
                        )
                    st.write(
                        "Triple Captain usually has greater upside in a double "
                        "gameweek with high expected minutes, but is not automatically optimal."
                    )

            st.markdown("#### Automated signal (heuristic)")
            optimized = st.session_state.get("optimized_squad")
            if optimized is not None:
                for label, value in chip_assessment(
                    squad, optimized, fixtures, team_map,
                    selected_gw, horizon, projection_col
                ):
                    st.write(f"**{label}:** {value}")
            else:
                st.info(
                    "Run the Squad Optimizer first to compare your current team "
                    "with an optimized Free Hit squad."
                )
        else:
            st.info("Load your team to assess chip usage and chip value.")

    with tab_players:
        st.subheader("Player database")
        f1, f2, f3, f4 = st.columns(4)

        with f1:
            pos_filter = st.multiselect(
                "Position", ["GK", "DEF", "MID", "FWD"],
                default=["GK", "DEF", "MID", "FWD"], key="pos_filter"
            )
        with f2:
            club_filter = st.multiselect(
                "Club", sorted(projected["team"].unique()), key="club_filter"
            )
        with f3:
            max_price = st.slider(
                "Maximum price (£m)", 3.5, 15.5, 15.5, 0.1
            )
        with f4:
            min_minutes = st.number_input(
                "Minimum minutes", 0, 4000, 0, 90
            )

        min_projection = st.slider(
            "Minimum horizon projection", 0.0, 50.0, 0.0, 0.5
        )

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

        cols = [
            "name", "team", "position", "Price", "form", "ppg", "points",
            "xP_next", "xP_horizon", "value_per_m", "selected_by",
            "availability", "news", "fixture_run"
        ]
        st.dataframe(
            view[cols].rename(columns={
                "name": "Player",
                "team": "Club",
                "position": "Pos",
                "form": "Form",
                "ppg": "Pts/G",
                "points": "Total pts",
                "xP_next": "xP next GW",
                "xP_horizon": "xP horizon",
                "value_per_m": "xP per £m",
                "selected_by": "Selected %",
                "availability": "Availability",
                "news": "FPL news",
                "fixture_run": "Fixtures",
            }),
            use_container_width=True,
            hide_index=True,
        )

    with tab_history:
        st.subheader("Manager history")
        if not profile:
            st.info("Enter your Team ID to view manager history.")
        else:
            h1, h2, h3 = st.columns(3)
            h1.metric(
                "Overall rank",
                f'{profile.get("summary_overall_rank"):,}'
                if profile.get("summary_overall_rank") else "—"
            )
            h2.metric(
                "Overall points",
                f'{profile.get("summary_overall_points"):,}'
                if profile.get("summary_overall_points") is not None else "—"
            )
            h3.metric(
                "Team value", money(profile.get("last_deadline_value", 0))
            )

            current_hist = history.get("current", []) if history else []
            if current_hist:
                hdf = pd.DataFrame(current_hist)
                keep = [
                    c for c in [
                        "event", "points", "total_points", "rank", "overall_rank",
                        "event_transfers", "event_transfers_cost", "points_on_bench",
                        "value", "bank"
                    ] if c in hdf
                ]
                st.dataframe(
                    hdf[keep].rename(columns={
                        "event": "GW",
                        "points": "GW points",
                        "total_points": "Total points",
                        "rank": "GW rank",
                        "overall_rank": "Overall rank",
                        "event_transfers": "Transfers",
                        "event_transfers_cost": "Hit",
                        "points_on_bench": "Bench points",
                        "value": "Team value (tenths)",
                        "bank": "Bank (tenths)",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
                if "points" in hdf:
                    st.line_chart(hdf.set_index("event")["points"])

            past = history.get("past", []) if history else []
            if past:
                st.markdown("**Previous seasons**")
                st.dataframe(
                    pd.DataFrame(past),
                    use_container_width=True,
                    hide_index=True,
                )

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

except requests.RequestException as exc:
    st.error(f"Unable to reach the FPL API right now: {exc}")
    st.info(
        "Try again later. The public FPL API can occasionally rate-limit "
        "requests or be unavailable."
    )

