from ortools.sat.python import cp_model
import pandas as pd
import os
from datetime import datetime
import time


class TimetableSolver:
    def __init__(self, model, all_vars, data):
        self.model = model
        self.all_vars = all_vars  # Structure ใหม่
        self.data = data
        self.solver = cp_model.CpSolver()
        self.last_output_path = None

    def solve(self):
        start_ts = time.time()
        start_dt = datetime.now()
        # ตั้งค่า Solver
        self.solver.parameters.max_time_in_seconds = 600.0
        self.solver.parameters.log_search_progress = True
        self.solver.parameters.relative_gap_limit = 0.03
        # self.solver.parameters.random_seed = 42

        # เพิ่มจำนวน Thread เพื่อช่วยประมวลผล (ถ้าเครื่องมีหลาย Core)
        self.solver.parameters.num_search_workers = 8

        print("--- Solving Model ---")
        status = self.solver.Solve(self.model)
        end_ts = time.time()
        end_dt = datetime.now()

        self.analyze_status(status)
        self._write_run_log(status, start_dt, end_dt, end_ts - start_ts)

        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            self.export_solution()
        elif status == cp_model.INFEASIBLE:
            self.report_infeasibility()

    def analyze_status(self, status):
        print("\n--- Solver Status ---")
        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN",
        }
        print(f"Status: {status_map.get(status, 'UNKNOWN')}")
        print(f"Conflicts: {self.solver.NumConflicts()}")
        print(f"Wall Time: {self.solver.WallTime()} s")

    def export_solution(self):
        print("\n--- Exporting Output ---")
        results = []

        courses = self.data["courses"]
        rooms = self.data["rooms"]
        time_slots = self.data.get("time_slots", [])

        for c in courses:
            c_id = c["id"]
            activities = self.all_vars[c_id]["activities"]
            for act_id, act in activities.items():
                # 1. ดึงเวลาเริ่มสอน
                start_slot = self.solver.Value(act["start"])
                duration = act["duration"]

                # 2. หาว่าสอนห้องไหน (วนดูว่าห้องไหนมีค่า presence == 1)
                assigned_room = "Unassigned"
                assigned_room_capacity = ""
                for r in rooms:
                    r_id = r["id"]
                    if self.solver.Value(act["rooms"][r_id]["is_present"]) == 1:
                        assigned_room = r_id
                        assigned_room_capacity = r.get("จำนวนที่นั่ง", "")
                        break

                start_label = (
                    time_slots[start_slot]["label"]
                    if time_slots and start_slot < len(time_slots)
                    else start_slot
                )
                end_slot = start_slot + duration

                # สร้าง Time Label แบบคอลัมน์เดียว: "Thursday 09:00-11:00"
                time_label = start_label
                if time_slots and start_slot < len(time_slots):
                    day = time_slots[start_slot]["day"]
                    start_min = time_slots[start_slot]["start_min"]
                    slot_minutes = self.data.get("time_config", {}).get(
                        "slot_minutes", 30
                    )
                    end_min = start_min + duration * slot_minutes
                    time_label = f"{day} {self._minutes_to_time(start_min)}-{self._minutes_to_time(end_min)}"

                results.append(
                    {
                        "Course_ID": c_id,
                        "Activity_ID": act_id,
                        "Activity_Type": act.get("type", ""),
                        "Course_Name": c.get("ชื่อวิชาภาษาอังกฤษ", c.get("name", "")),
                        "Enrollment": c.get("ลง", ""),
                        "Room_ID": assigned_room,
                        "Room_Capacity": assigned_room_capacity,
                        "Start_Slot": start_slot,
                        "End_Slot": end_slot,
                        "Time_Label": time_label,
                        "Teacher": ",".join(c.get("teacher_list", [])),
                    }
                )

        if results:
            df_out = pd.DataFrame(results)
            os.makedirs("output", exist_ok=True)
            output_path = self._next_versioned_output_path("output")
            df_out.to_csv(output_path, index=False)
            self.last_output_path = output_path
            print(f"Saved result to: {output_path}")
            print(df_out.head())
        else:
            print("No results generated.")

    def report_infeasibility(self):
        """
        Placeholder สำหรับการวิเคราะห์สาเหตุที่ทำให้ INFEASIBLE
        TODO: เพิ่มตัวแปรอธิบาย (assumptions) หรือ conflict refiner ในอนาคต
        """
        print("\n--- Infeasibility Report ---")
        print("Model is INFEASIBLE.")
        core = self.solver.SufficientAssumptionsForInfeasibility()
        if core:
            print("Unsat Core Assumptions:")
            names = [v.Name() for v in core]
            print(", ".join(names))

            details = self.data.get("assumption_details", {})
            if details:
                print("\n[Unsat Core Details]")
                for n in names:
                    if n in details:
                        print(f"- {n}: {details[n]}")
        else:
            print("No unsat core available.")

    def _minutes_to_time(self, minutes):
        h = minutes // 60
        m = minutes % 60
        return f"{h:02d}:{m:02d}"

    def _next_versioned_output_path(self, output_dir):
        """
        สร้างชื่อไฟล์แบบวิ่งเลขเวอร์ชัน: Schdule_Result_V.1.csv, Schdule_Result_V.2.csv, ...
        """
        prefix = "Schdule_Result_V."
        existing = []
        for fname in os.listdir(output_dir):
            if fname.startswith(prefix) and fname.endswith(".csv"):
                num_str = fname[len(prefix) : -4]
                if num_str.isdigit():
                    existing.append(int(num_str))

        next_ver = (max(existing) + 1) if existing else 1
        return os.path.join(output_dir, f"{prefix}{next_ver}.csv")

    def _next_versioned_log_path(self, output_dir):
        prefix = "Schdule_Result_V."
        existing = []
        for fname in os.listdir(output_dir):
            if fname.startswith(prefix) and fname.endswith(".md"):
                num_str = fname[len(prefix) : -3]
                if num_str.isdigit():
                    existing.append(int(num_str))

        next_ver = (max(existing) + 1) if existing else 1
        return os.path.join(output_dir, f"{prefix}{next_ver}.md")

    def _write_run_log(self, status, start_dt, end_dt, elapsed_sec):
        log_dir = os.path.join("output", "logs")
        os.makedirs(log_dir, exist_ok=True)
        if self.last_output_path:
            base = os.path.basename(self.last_output_path).replace(".csv", ".md")
            log_path = os.path.join(log_dir, base)
        else:
            log_path = self._next_versioned_log_path(log_dir)

        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN",
        }

        try:
            objective_val = self.solver.ObjectiveValue()
        except Exception:
            objective_val = "NA"

        lines = [
            "# Solver Run Log",
            "",
            f"- Start: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- End: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Elapsed (s): {elapsed_sec:.6f}",
            "",
            "## Solver Parameters",
            f"- max_time_in_seconds: {self.solver.parameters.max_time_in_seconds}",
            f"- log_search_progress: {self.solver.parameters.log_search_progress}",
            f"- num_search_workers: {self.solver.parameters.num_search_workers}",
            "",
            "## Status",
            f"- status: {status_map.get(status, 'UNKNOWN')}",
            f"- objective: {objective_val}",
            f"- conflicts: {self.solver.NumConflicts()}",
            f"- branches: {self.solver.NumBranches()}",
            f"- wall_time (s): {self.solver.WallTime()}",
        ]

        if status == cp_model.INFEASIBLE:
            core = self.solver.SufficientAssumptionsForInfeasibility()
            if core:
                lines.append("")
                lines.append("## Unsat Core Assumptions")
                names = [v.Name() for v in core]
                lines.append(", ".join(names))
                details = self.data.get("assumption_details", {})
                if details:
                    lines.append("")
                    lines.append("## Unsat Core Details")
                    for n in names:
                        if n in details:
                            lines.append(f"- {n}: {details[n]}")

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
