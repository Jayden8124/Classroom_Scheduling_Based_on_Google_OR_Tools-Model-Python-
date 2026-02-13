import os
import glob
import pandas as pd


def _find_latest_schedule():
    candidates = glob.glob(os.path.join("output", "Schdule_Result_V.*.csv"))
    if not candidates:
        # fallback to legacy name
        legacy = os.path.join("output", "schedule_result.csv")
        return legacy if os.path.exists(legacy) else None

    def _ver(path):
        base = os.path.basename(path)
        # Schdule_Result_V.12.csv
        try:
            v = base.split("Schdule_Result_V.")[1].split(".csv")[0]
            return int(v)
        except Exception:
            return -1

    candidates.sort(key=_ver, reverse=True)
    return candidates[0]


def _preview_courses(df_courses, year):
    # ตามปีที่เลือก
    df_y = df_courses[df_courses["ชั้นปี"].astype(str).str.strip() == str(year)].copy()
    # เลือก unique course_code + name
    df_unique = (
        df_y[["รหัสวิชา", "ชื่อวิชาภาษาอังกฤษ"]]
        .dropna()
        .drop_duplicates()
        .reset_index(drop=True)
    )
    df_unique.index.name = "index"
    print(f"\n[Preview] Courses (Year {year}): index, course_code, name_eng")
    for idx, row in df_unique.iterrows():
        print(f"{idx:03d} | {row['รหัสวิชา']} | {row['ชื่อวิชาภาษาอังกฤษ']}")
    return df_unique


def _parse_indices(raw, max_idx):
    raw = raw.strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    idxs = []
    for p in parts:
        if p.isdigit():
            i = int(p)
            if 0 <= i <= max_idx:
                idxs.append(i)
    return sorted(set(idxs))


def _find_conflicts(df_sched):
    conflicts = []
    rows = df_sched.sort_values(["Start_Slot", "End_Slot"]).reset_index(drop=True)
    for i in range(len(rows)):
        a = rows.iloc[i]
        for j in range(i + 1, len(rows)):
            b = rows.iloc[j]
            # overlap in global slot timeline
            if a["Start_Slot"] < b["End_Slot"] and b["Start_Slot"] < a["End_Slot"]:
                conflicts.append((a, b))
    return conflicts


def main():
    courses_path = os.path.join("data", "Comsci_Test.csv")
    schedule_path = _find_latest_schedule()

    if not os.path.exists(courses_path):
        print(f"Error: {courses_path} not found.")
        return

    if not schedule_path or not os.path.exists(schedule_path):
        print("Error: schedule result file not found in output/.")
        return

    df_courses = pd.read_csv(courses_path, dtype=str)

    years = sorted(
        set(df_courses["ชั้นปี"].astype(str).str.strip().dropna().tolist())
    )
    print("\nAvailable Years:", ", ".join(years))
    year_raw = input("Select year (e.g., 1, 2, 3, 4): ").strip()
    if not year_raw:
        print("No year selected.")
        return
    if year_raw not in years:
        print("Invalid year selected.")
        return

    df_unique = _preview_courses(df_courses, year_raw)

    raw = input(
        "\nEnter course indices (comma-separated) to test conflicts: "
    ).strip()
    idxs = _parse_indices(raw, df_unique.index.max())
    if not idxs:
        print("No valid indices selected.")
        return

    selected = df_unique.loc[idxs]
    selected_codes = set(selected["รหัสวิชา"].astype(str).str.strip())
    print("\n[Selected Courses]")
    for _, row in selected.iterrows():
        print(f"- {row['รหัสวิชา']} | {row['ชื่อวิชาภาษาอังกฤษ']}")

    df_sched = pd.read_csv(schedule_path, dtype=str)
    # ใช้เฉพาะปี 1 และรหัสวิชาที่เลือก
    df_sched = df_sched[df_sched["Course_ID"].astype(str).str.contains(f"_Y{year_raw}")]
    df_sched = df_sched[
        df_sched["Course_ID"].astype(str).str.split("_").str[0].isin(selected_codes)
    ]

    # แปลง slot เป็น int
    df_sched["Start_Slot"] = df_sched["Start_Slot"].astype(int)
    df_sched["End_Slot"] = df_sched["End_Slot"].astype(int)

    if df_sched.empty:
        print("No scheduled activities found for selected courses.")
        return

    conflicts = _find_conflicts(df_sched)
    if not conflicts:
        print("\nResult: No conflicts found for selected courses.")
        return

    print("\n[Conflicts Found]")
    for a, b in conflicts:
        print(
            f"- {a['Course_Name']} ({a['Activity_ID']}) [{a['Time_Label']}] "
            f"vs {b['Course_Name']} ({b['Activity_ID']}) [{b['Time_Label']}]"
        )


if __name__ == "__main__":
    main()
