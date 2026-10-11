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
