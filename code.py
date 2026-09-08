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
    page_title="Grid-Locked Corridor Travel Time Tracker", layout="wide"
)

st.title("🚗 Grid-Locked Corridor Travel Time Tracker")
st.markdown(
    "Strict corridor tracking using programmatically generated intersections"
    " to prevent detours."
)


# Helper function to generate strict grid waypoints along a numbered corridor
def generate_linden_waypoints(start_num, end_num, reverse=False):
  waypoints = []
  step = 15  # Drop a mandatory checkpoint every 15 blocks

  if not reverse:
    curr = start_num + step
    while curr < end_num:
      waypoints.append(f"Linden Blvd & {curr}th St, Queens, NY")
      curr += step
  else:
    curr = start_num - step
    while curr > end_num:
      waypoints.append(f"Linden Blvd & {curr}th St, Queens, NY")
      curr -= step

  return waypoints


# Function to query travel time with strict corridor enforcement
def fetch_corridor_travel_time(
    api_key, origin, destination, start_num, end_num, reverse=False
):
  url = "https://maps.googleapis.com/maps/api/directions/json"

  # Generate strict checkpoints strictly on Linden Blvd
  raw_waypoints = generate_linden_waypoints(start_num, end_num, reverse)
  formatted_waypoints = (
      "|".join([f"via:{wp}" for wp in raw_waypoints]) if raw_waypoints else ""
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
      return round(total_duration / 60)
  except Exception as e:
    print(f"API Error: {e}")

  return None


# Sidebar Setup
with st.sidebar:
  st.header("⚙️ Corridor Settings")
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
      "Dir 1 Start Intersection", value="Linden Blvd & 113th St, Queens, NY"
  )
  dir1_dest = st.text_input(
      "Dir 1 End Intersection", value="Linden Blvd & 235th St, Queens, NY"
  )
  dir1_start_num = st.number_input("Dir 1 Start Street Number", value=113)
  dir1_end_num = st.number_input("Dir 1 End Street Number", value=235)

  st.markdown("---")
  st.subheader(f"Direction 2 ({label_dir2})")
  dir2_origin = st.text_input(
      "Dir 2 Start Intersection", value="Linden Blvd & 235th St, Queens, NY"
  )
  dir2_dest = st.text_input(
      "Dir 2 End Intersection", value="Linden Blvd & 113th St, Queens, NY"
  )
  dir2_start_num = st.number_input("Dir 2 Start Street Number", value=235)
  dir2_end_num = st.number_input("Dir 2 End Street Number", value=113)

col_name_1 = f"{label_dir1} Time"
col_name_2 = f"{label_dir2} Time"

if not os.path.exists(CSV_FILE):
  df_init = pd.DataFrame(columns=["Timestamp", col_name_1, col_name_2])
  df_init.to_csv(CSV_FILE, index=False)


def background_logger():
  while st.session_state.get("is_running", False):
    if api_key:
      time_1 = fetch_corridor_travel_time(
          api_key,
          dir1_origin,
          dir1_dest,
          dir1_start_num,
          dir1_end_num,
          reverse=False,
      )
      time_2 = fetch_corridor_travel_time(
          api_key, dir2_origin, dir2_dest, dir2_start_num, dir2_end_num, reverse=True
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

# Main Layout Tabs
tab_tracker, tab_maps = st.tabs(
    ["📝 Automation & Data Table", "🗺️ Strict Corridor Map Links"]
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

          t1 = fetch_corridor_travel_time(
              api_key,
              dir1_origin,
              dir1_dest,
              dir1_start_num,
              dir1_end_num,
              reverse=False,
          )
          t2 = fetch_corridor_travel_time(
              api_key,
              dir2_origin,
              dir2_dest,
              dir2_start_num,
              dir2_end_num,
              reverse=True,
          )
          if t1 and t2:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pd.DataFrame(
                [[ts, f"{t1} mins", f"{t2} mins"]],
                columns=["Timestamp", col_name_1, col_name_2],
            ).to_csv(CSV_FILE, mode="a", header=False, index=False)

          threading.Thread(target=background_logger, daemon=True).start()
          st.success("Automation started with 100% Linden Blvd grid lock!")
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
  st.subheader("🗺️ Verify Strict Route Links")
  st.markdown(
      "Click below to open your strict Linden Boulevard route in Google Maps"
      " and verify that every waypoint stays precisely on the corridor:"
  )


  # Generate web-friendly strict URL
  def make_url(origin, dest, nums, rev):
    wps = generate_linden_waypoints(nums[0], nums[1], rev)
    parts = [origin] + wps + [dest]
    return (
        "https://www.google.com/maps/dir/"
        + "/".join([p.replace(" ", "+") for p in parts])
    )


  url_1 = make_url(
      dir1_origin, dir1_dest, (dir1_start_num, dir1_end_num), False
  )
  url_2 = make_url(
      dir2_origin, dir2_dest, (dir2_start_num, dir2_end_num), True
  )

  m1, m2 = st.columns(2)
  with m1:
    st.markdown(
        f'<a href="{url_1}" target="_blank"><button'
        ' style="background-color:#4CAF50; color:white; padding:12px 24px;'
        ' border:none; border-radius:4px; cursor:pointer; width:100%;'
        ' font-weight:bold;">🗺️ Open Strict '
        f"{label_dir1} Map</button></a>",
        unsafe_allow_html=True,
    )
  with m2:
    st.markdown(
        f'<a href="{url_2}" target="_blank"><button'
        ' style="background-color:#2196F3; color:white; padding:12px 24px;'
        ' border:none; border-radius:4px; cursor:pointer; width:100%;'
        ' font-weight:bold;">🗺️ Open Strict '
        f"{label_dir2} Map</button></a>",
        unsafe_allow_html=True,
    )
