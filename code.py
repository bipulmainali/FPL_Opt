from datetime import datetime
import os
import re
import threading
import time
import pandas as pd
import requests
import streamlit as st

CSV_FILE = "automatic_corridor_times.csv"

# Page Layout
st.set_page_config(
    page_title="Universal Automated Corridor Travel Time Logger", layout="wide"
)

st.title("🚗 Universal Automated Corridor Travel Time Tracker")
st.markdown(
    "Paste any Google Maps directions URL below. The app dynamically tracks that"
    " corridor automatically."
)


# Helper function to extract start and end points from a Google Maps URL
def extract_endpoints_from_url(url):
  try:
    if "/maps/dir/" in url:
      path_part = url.split("/maps/dir/")[1]
      parts = path_part.split("/")
      if len(parts) >= 2:
        origin = parts[0].replace("+", " ")
        destination = parts[1].replace("+", " ")
        return origin, destination
  except Exception:
    pass
  return None, None


# Function to fetch live traffic time via API
def fetch_travel_time(api_key, origin, destination):
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
    if data.get("status") == "OK":
      total_duration = 0
      for leg in data["routes"][0]["legs"]:
        if "duration_in_traffic" in leg:
          total_duration += leg["duration_in_traffic"]["value"]
        else:
          total_duration += leg["duration"]["value"]
      return round(total_duration / 60)  # Minutes
    else:
      print(f"API Error Status: {data.get('status')}")
  except Exception as e:
    print(f"Request Error: {e}")
  return None


# Sidebar Setup
with st.sidebar:
  st.header("⚙️ Settings & URLs")
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
  default_url_1 = "https://www.google.com/maps/dir/Linden+Blvd+%26+113th+St,+Queens,+NY+11420/Linden+Blvd%2F235+St,+Queens,+NY+11411"
  dir1_url = st.text_area(
      f"Direction 1 ({label_dir1}) Map URL", value=default_url_1, height=90
  )

  st.markdown("---")
  default_url_2 = "https://www.google.com/maps/dir/Linden+Blvd+%26+235+St,+Queens,+NY+11411/Linden+Blvd+%26+113th+St,+Queens,+NY+11420"
  dir2_url = st.text_area(
      f"Direction 2 ({label_dir2}) Map URL", value=default_url_2, height=90
  )

col_name_1 = f"{label_dir1} Time"
col_name_2 = f"{label_dir2} Time"

if not os.path.exists(CSV_FILE):
  df_init = pd.DataFrame(columns=["Timestamp", col_name_1, col_name_2])
  df_init.to_csv(CSV_FILE, index=False)


def background_logger():
  while st.session_state.get("is_running", False):
    if api_key:
      o1, d1 = extract_endpoints_from_url(dir1_url)
      o2, d2 = extract_endpoints_from_url(dir2_url)

      time_1 = fetch_travel_time(api_key, o1, d1) if o1 and d1 else None
      time_2 = fetch_travel_time(api_key, o2, d2) if o2 and d2 else None

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
        o1, d1 = extract_endpoints_from_url(dir1_url)
        o2, d2 = extract_endpoints_from_url(dir2_url)

        if not o1 or not d1 or not o2 or not d2:
          st.error(
              "Could not parse endpoints from the URLs. Please ensure they are"
              " valid Google Maps directions links."
          )
        else:
          st.session_state.is_running = True

          t1 = fetch_travel_time(api_key, o1, d1)
          t2 = fetch_travel_time(api_key, o2, d2)
          if t1 and t2:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pd.DataFrame(
                [[ts, f"{t1} mins", f"{t2} mins"]],
                columns=["Timestamp", col_name_1, col_name_2],
            ).to_csv(CSV_FILE, mode="a", header=False, index=False)

          threading.Thread(target=background_logger, daemon=True).start()
          st.success("Automation started for corridor! First row recorded.")
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

  st.markdown("---")
  # Clear Recordings Button
  if st.button(
      "🗑️ Clear All Recordings", type="secondary", use_container_width=True
  ):
    st.session_state.is_running = False  # Stops background runner if active
    if os.path.exists(CSV_FILE):
      os.remove(CSV_FILE)
    # Re-initialize clean CSV file with current column names
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
