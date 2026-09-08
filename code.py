from datetime import datetime
import os
import threading
import time
import pandas as pd
import requests
import streamlit as st

CSV_FILE = "automatic_corridor_times.csv"

# Page Layout
st.set_page_config(page_title="Automated Corridor Travel Time Logger", layout="wide")

st.title("🚗 Fully Automated Corridor Travel Time Tracker")
st.markdown(
    "Select your corridor orientation, paste your Google Maps URLs, and let"
    " the app log traffic automatically."
)

# --- INTERNAL ROUTE DEFINITIONS (Precise coordinates for Linden Blvd) ---
EB_ORIGIN = "40.6773342,-73.8254692"  # 113th St
EB_DESTINATION = "40.69078,-73.72742"  # 235th St
EB_WAYPOINTS = [
    "40.6795076,-73.8178294",
    "40.6823885,-73.8077585",
    "40.6852347,-73.7976948",
    "40.6896298,-73.7840872",
    "40.6928388,-73.7707361",
    "40.6948363,-73.7508502",
    "40.6936296,-73.7353064",
]

WB_ORIGIN = "40.69078,-73.72742"
WB_DESTINATION = "40.6773342,-73.8254692"
WB_WAYPOINTS = list(reversed(EB_WAYPOINTS))


# Function to fetch live traffic time via API
def fetch_google_travel_time(api_key, origin, destination, waypoints_list):
  url = "https://maps.googleapis.com/maps/api/directions/json"
  formatted_waypoints = (
      "|".join([f"via:{wp}" for wp in waypoints_list])
      if waypoints_list
      else ""
  )

  params = {
      "origin": origin,
      "destination": destination,
      "waypoints": formatted_waypoints,
      "departure_time": "now",
      "mode": "driving",
      "key": api_key,
  }

  try:
    response = requests.get(url, params=params)
    data = response.json()
    if data.get("status") == "OK":
      total_duration = 0
      for leg in data["routes"][0]["legs"]:
        if "duration_in_traffic" in leg:
          total_duration += leg["duration_in_traffic"]["value"]
        else:
          total_duration += leg["duration"]["value"]
      return round(total_duration / 60)  # Minutes
  except Exception as e:
    print(f"API Error: {e}")
  return None


# Sidebar Setup
with st.sidebar:
  st.header("⚙️ Settings & Configuration")
  api_key = st.text_input("Google Maps API Key", type="password")

  interval_mins = st.selectbox(
      "Check Interval (Minutes)", [5, 10, 15, 20, 30, 45, 60], index=2
  )

  # Direction Type Dropdown
  direction_mode = st.selectbox(
      "Select Direction Orientation", ["EB/WB", "NB/SB"]
  )

  # Dynamic labels based on dropdown selection
  if direction_mode == "EB/WB":
    label_dir1 = "Eastbound"
    label_dir2 = "Westbound"
  else:
    label_dir1 = "Northbound"
    label_dir2 = "Southbound"

  st.markdown("---")
  default_url_1 = (
      "https://www.google.com/maps/dir/Linden+Blvd+%26+113th+St..."
  )
  dir1_url = st.text_area(
      f"Direction 1 ({label_dir1}) Map URL", value=default_url_1, height=90
  )

  st.markdown("---")
  default_url_2 = (
      "https://www.google.com/maps/dir/Linden+Blvd+%26+235+St..."
  )
  dir2_url = st.text_area(
      f"Direction 2 ({label_dir2}) Map URL", value=default_url_2, height=90
  )

# Define columns dynamically based on selected orientation
col_name_1 = f"{label_dir1} Time"
col_name_2 = f"{label_dir2} Time"

# Initialize CSV file if not present or match headers
if not os.path.exists(CSV_FILE):
  df_init = pd.DataFrame(columns=["Timestamp", col_name_1, col_name_2])
  df_init.to_csv(CSV_FILE, index=False)


# Background monitoring loop function
def background_logger():
  while st.session_state.get("is_running", False):
    if api_key:
      time_1 = fetch_google_travel_time(
          api_key, EB_ORIGIN, EB_DESTINATION, EB_WAYPOINTS
      )
      time_2 = fetch_google_travel_time(
          api_key, WB_ORIGIN, WB_DESTINATION, WB_WAYPOINTS
      )

      if time_1 is not None and time_2 is not None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_row = pd.DataFrame(
            [[timestamp, f"{time_1} mins", f"{time_2} mins"]],
            columns=["Timestamp", col_name_1, col_name_2],
        )
        new_row.to_csv(CSV_FILE, mode="a", header=False, index=False)

    time.sleep(interval_mins * 60)


if "is_running" not in st.session_state:
  st.session_state.is_running = False

# Main Layout
col_ctrl, col_table = st.columns([1, 2])

with col_ctrl:
  st.subheader("🚀 Automation Control")

  if not st.session_state.is_running:
    if st.button(
        "Start Automatic Recording", type="primary", use_container_width=True
    ):
      if not api_key:
        st.error("Please enter your Google Maps API key in the sidebar.")
      else:
        st.session_state.is_running = True

        # Immediate first fetch on click
        t1 = fetch_google_travel_time(
            api_key, EB_ORIGIN, EB_DESTINATION, EB_WAYPOINTS
        )
        t2 = fetch_google_travel_time(
            api_key, WB_ORIGIN, WB_DESTINATION, WB_WAYPOINTS
        )
        if t1 and t2:
          ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
          pd.DataFrame(
              [[ts, f"{t1} mins", f"{t2} mins"]],
              columns=["Timestamp", col_name_1, col_name_2],
          ).to_csv(CSV_FILE, mode="a", header=False, index=False)

        threading.Thread(target=background_logger, daemon=True).start()
        st.success("Automation started! First row recorded.")
        st.rerun()
  else:
    if st.button(
        "Stop Automatic Recording", type="secondary", use_container_width=True
    ):
      st.session_state.is_running = False
      st.warning("Automation stopped.")
      st.rerun()

    st.info(
        f"🟢 **Status:** Running. Checking traffic every {interval_mins} minutes."
    )

with col_table:
  st.subheader("📊 Live Data Table")

  if os.path.exists(CSV_FILE):
    df = pd.read_csv(CSV_FILE)
    # Update table columns dynamically if orientation was switched
    if len(df.columns) == 3:
      df.columns = ["Timestamp", col_name_1, col_name_2]

    st.dataframe(df, use_container_width=True)

    if not df.empty:
      csv_data = df.to_csv(index=False).encode("utf-8")
      st.download_button(
          label="📥 Download Table as CSV",
          data=csv_data,
          file_name="corridor_travel_times.csv",
          mime="text/csv",
      )
  else:
    st.write("No recorded entries yet. Click 'Start Automatic Recording'.")
