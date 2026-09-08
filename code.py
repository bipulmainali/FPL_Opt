from datetime import datetime
import os
import threading
import time
import pandas as pd
import requests
import streamlit as st

CSV_FILE = "automatic_corridor_times.csv"

# Page Layout
st.set_page_config(
    page_title="Auto-Segmented Corridor Travel Time & Map Tracker", layout="wide"
)

st.title("🚗 Auto-Segmented Corridor Travel Time & Live Map Tracker")
st.markdown(
    "Automated traffic logging with micro-segment anti-detour protection and"
    " side-by-side live maps."
)


# Helper function to query a route, auto-break it into micro-segments, and sum travel times
def fetch_segmented_travel_time(api_key, origin, destination):
  url = "https://maps.googleapis.com/maps/api/directions/json"
  params = {
      "origin": origin,
      "destination": destination,
      "departure_time": "now",
      "mode": "driving",
      "key": api_key,
  }

  try:
    response = requests.get(url, params=params)
    data = response.json()
    if data.get("status") != "OK":
      return None

    steps = data["routes"][0]["legs"][0]["steps"]
    micro_waypoints = []
    accumulated_distance = 0
    for step in steps:
      accumulated_distance += step["distance"]["value"]
      if accumulated_distance >= 1500:  # Every ~1 mile to lock path
        end_loc = step["end_location"]
        micro_waypoints.append(f"{end_loc['lat']},{end_loc['lng']}")
        accumulated_distance = 0

    formatted_waypoints = (
        "|".join([f"via:{wp}" for wp in micro_waypoints])
        if micro_waypoints
        else ""
    )
    chunk_params = {
        "origin": origin,
        "destination": destination,
        "waypoints": formatted_waypoints,
        "departure_time": "now",
        "mode": "driving",
        "key": api_key,
    }

    chunk_response = requests.get(url, params=chunk_params)
    chunk_data = chunk_response.json()

    if chunk_data.get("status") == "OK":
      total_duration = 0
      for leg in chunk_data["routes"][0]["legs"]:
        if "duration_in_traffic" in leg:
          total_duration += leg["duration_in_traffic"]["value"]
        else:
          total_duration += leg["duration"]["value"]
      return round(total_duration / 60)

  except Exception as e:
    print(f"API Error: {e}")

  return None


# Sidebar Setup
with st.sidebar:
  st.header("⚙️ Settings & Corridor Setup")
  api_key = st.text_input("Google Maps API Key", type="password")

  interval_mins = st.selectbox(
      "Check Interval (Minutes)", [5, 10, 15, 20, 30, 45, 60], index=2
  )

  direction_mode = st.selectbox(
      "Select Direction Orientation", ["EB/WB", "NB/SB"]
  )

  if direction_mode == "EB/WB":
    label_dir1 = "Eastbound"
    label_dir2 = "Westbound"
  else:
    label_dir1 = "Northbound"
    label_dir2 = "Southbound"

  st.markdown("---")
  st.subheader(f"Direction 1 ({label_dir1})")
  dir1_origin = st.text_input(
      "Dir 1 Start", value="Linden Blvd & 113th St, Queens, NY 11420"
  )
  dir1_destination = st.text_input(
      "Dir 1 End", value="Linden Blvd & 235th St, Queens, NY 11411"
  )

  st.markdown("---")
  st.subheader(f"Direction 2 ({label_dir2})")
  dir2_origin = st.text_input(
      "Dir 2 Start", value="Linden Blvd & 235th St, Queens, NY 11411"
  )
  dir2_destination = st.text_input(
      "Dir 2 End", value="Linden Blvd & 113th St, Queens, NY 11420"
  )

col_name_1 = f"{label_dir1} Time"
col_name_2 = f"{label_dir2} Time"

if not os.path.exists(CSV_FILE):
  df_init = pd.DataFrame(columns=["Timestamp", col_name_1, col_name_2])
  df_init.to_csv(CSV_FILE, index=False)


def background_logger():
  while st.session_state.get("is_running", False):
    if api_key:
      time_1 = fetch_segmented_travel_time(api_key, dir1_origin, dir1_destination)
      time_2 = fetch_segmented_travel_time(api_key, dir2_origin, dir2_destination)

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

# Main Layout Tabs
tab_tracker, tab_maps = st.tabs(
    ["📝 Automation & Data Table", "🗺️ Side-by-Side Live Maps"]
)

with tab_tracker:
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

          t1 = fetch_segmented_travel_time(
              api_key, dir1_origin, dir1_destination
          )
          t2 = fetch_segmented_travel_time(
              api_key, dir2_origin, dir2_destination
          )
          if t1 and t2:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pd.DataFrame(
                [[ts, f"{t1} mins", f"{t2} mins"]],
                columns=["Timestamp", col_name_1, col_name_2],
            ).to_csv(CSV_FILE, mode="a", header=False, index=False)

          threading.Thread(target=background_logger, daemon=True).start()
          st.success("Automation started with anti-detour protection!")
          st.rerun()
    else:
      if st.button(
          "Stop Automatic Recording", type="secondary", use_container_width=True
      ):
        st.session_state.is_running = False
        st.warning("Automation stopped.")
        st.rerun()

      st.info(
          f"🟢 **Status:** Running. Checking traffic every {interval_mins}"
          " minutes."
      )

    st.markdown("---")
    if st.button(
        "🗑️ Clear All Recordings", type="secondary", use_container_width=True
    ):
      st.session_state.is_running = False
      if os.path.exists(CSV_FILE):
        os.remove(CSV_FILE)
      df_init = pd.DataFrame(columns=["Timestamp", col_name_1, col_name_2])
      df_init.to_csv(CSV_FILE, index=False)
      st.success("Recording history cleared successfully!")
      st.rerun()

  with col_table:
    st.subheader("📊 Live Data Table")

    if os.path.exists(CSV_FILE):
      df = pd.read_csv(CSV_FILE)
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

with tab_maps:
  st.subheader("🗺️ Live Route Visualizer (Side-by-Side)")
  if api_key:
    map_col1, map_col2 = st.columns(2)

    with map_col1:
      st.markdown(f"**{label_dir1} Corridor**")
      embed_url_1 = f"https://www.google.com/maps/embed/v1/directions?key={api_key}&origin={requests.utils.quote(dir1_origin)}&destination={requests.utils.quote(dir1_destination)}&mode=driving"
      st.markdown(
          f'<iframe width="100%" height="500" style="border:0"'
          f' loading="lazy" allowfullscreen src="{embed_url_1}"></iframe>',
          unsafe_allow_html=True,
      )

    with map_col2:
      st.markdown(f"**{label_dir2} Corridor**")
      embed_url_2 = f"https://www.google.com/maps/embed/v1/directions?key={api_key}&origin={requests.utils.quote(dir2_origin)}&destination={requests.utils.quote(dir2_destination)}&mode=driving"
      st.markdown(
          f'<iframe width="100%" height="500" style="border:0"'
          f' loading="lazy" allowfullscreen src="{embed_url_2}"></iframe>',
          unsafe_allow_html=True,
      )
  else:
    st.warning(
        "Please enter your Google Maps API Key in the sidebar to render the"
        " side-by-side interactive maps."
    )
