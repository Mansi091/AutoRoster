"""Clean up the geographic coordinates for all 76 stops to their true PCMC/Talegaon/Chakan locations,
removing false matches (like Nanded City or Kothrud or Central Pune).
"""
import pandas as pd
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Accurate local coordinates for the Pune Northwest industrial belt
LOCAL_ANCHORS = {
    # Talegaon / Maval area (lat ~18.73, lon ~73.68)
    "talegaon": (18.7350, 73.6750),
    "ambi": (18.7280, 73.6920),
    "varale": (18.7420, 73.6980),
    "eco city": (18.7420, 73.6980),
    "katvi": (18.7320, 73.6650),
    "vadgaon": (18.7480, 73.6450),
    "kamshet": (18.7550, 73.5600),
    "lonavala": (18.7510, 73.4750),
    "indori": (18.7470, 73.7080),
    "darumbre": (18.7150, 73.6850),
    "durumbe": (18.7150, 73.6850),
    "bebedohal": (18.7200, 73.6500),
    "kadadhe": (18.7250, 73.5800),

    # PCMC / Ravet / Nigdi / Dehu area (lat ~18.65, lon ~73.76)
    "ravet": (18.6540, 73.7420),
    "punawale": (18.6380, 73.7460),
    "walhekarwadi": (18.6380, 73.7580),
    "welhekarwadi": (18.6380, 73.7580),
    "nigdi": (18.6520, 73.7750),
    "nigadi": (18.6520, 73.7750),
    "akurdi": (18.6480, 73.7820),
    "chinchwad": (18.6280, 73.7920),
    "pimpri": (18.6250, 73.8050),
    "dehu": (18.7200, 73.7660),
    "dehugaon": (18.7200, 73.7660),
    "hinjwadi": (18.5980, 73.7380),
    "kalewadi": (18.6180, 73.7850),
    "sangvi": (18.5820, 73.8150),
    "dange chowk": (18.6150, 73.7680),

    # Chakan / Moshi / Bhosari area (lat ~18.72, lon ~73.85)
    "chakan": (18.7550, 73.8550),
    "ambethan": (18.7580, 73.8580),
    "moshi": (18.6750, 73.8450),
    "mosi": (18.6750, 73.8450),
    "bhosari": (18.6420, 73.8480),
    "chimbli": (18.7050, 73.8600),
    "rajgurunagar": (18.8550, 73.8850),
    "khed": (18.8550, 73.8850),
    "chikhali": (18.6820, 73.8120),
    "charoli": (18.6450, 73.8820),
}

def correct_all_master_coordinates():
    path = ROOT / "output/04_pickup_point_master.xlsx"
    df = pd.read_excel(path, sheet_name="Pickup Point Master")

    for idx, row in df.iterrows():
        name = str(row["pickup_point"]).lower()
        key = str(row["pickup_key"]).lower()
        curr_lat = row["latitude"]
        curr_lon = row["longitude"]

        # If it matched to generic Pune centre (18.5204, 73.8567) or far away, fix it to local landmark
        is_generic_pune = (abs(curr_lat - 18.5204) < 0.05 and abs(curr_lon - 73.8567) < 0.05)
        is_far_outlier = (curr_lon > 74.0 or curr_lat > 19.0 or curr_lat < 18.5)

        for anchor, (a_lat, a_lon) in LOCAL_ANCHORS.items():
            if anchor in name or anchor in key:
                if is_generic_pune or is_far_outlier or ("nanded" in str(row.get("coordinate_source", "")).lower()) or ("kothrud" in str(row.get("coordinate_source", "")).lower()):
                    df.at[idx, "latitude"] = a_lat
                    df.at[idx, "longitude"] = a_lon
                    df.at[idx, "coordinate_source"] = f"True local anchor: {anchor.title()}"
                break

    # Save back to output and manual
    writer = pd.ExcelWriter(path, engine="openpyxl")
    df.to_excel(writer, sheet_name="Pickup Point Master", index=False)
    ready = df[df["ready_for_routing"] == "Yes"].copy()
    ready["latitude"] = df["latitude"]
    ready["longitude"] = df["longitude"]
    ready.to_excel(writer, sheet_name="Ready for Routing", index=False)
    writer.close()

    # Also update manual master
    man_path = ROOT / "data/manual/pickup_point_master.xlsx"
    man_df = pd.read_excel(man_path)
    for idx, row in df.iterrows():
        k = row["pickup_key"]
        man_df.loc[man_df["pickup_key"] == k, "latitude"] = row["latitude"]
        man_df.loc[man_df["pickup_key"] == k, "longitude"] = row["longitude"]
        man_df.loc[man_df["pickup_key"] == k, "status"] = "Approved"
    man_writer = pd.ExcelWriter(man_path, engine="openpyxl")
    man_df.to_excel(man_writer, sheet_name="Pickup Point Master", index=False)
    man_writer.close()
    print("Cleaned up coordinates for all local stops!")

if __name__ == "__main__":
    correct_all_master_coordinates()
