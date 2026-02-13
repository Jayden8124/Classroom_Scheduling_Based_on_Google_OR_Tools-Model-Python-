from ortools.sat.python import cp_model
from src.constraints import Constraints


class TimetableModel:
    def __init__(self, data):
        self.data = data
        self.model = cp_model.CpModel()

        # all_vars จะเปลี่ยนโครงสร้างเป็น Dictionary ที่ซับซ้อนขึ้นแต่มีประสิทธิภาพสูง
        self.all_vars = {}

    def create_variables(self):
        print("Creating Variables (Interval-based)...")

        courses = self.data["courses"]
        rooms = self.data["rooms"]
        time_slots = self.data.get("time_slots", [])

        # จำนวนคาบทั้งหมด (Time Slots)
        # ถ้าไม่มีข้อมูล ให้ใช้ fallback เพื่อไม่ให้ crash
        horizon = len(time_slots) if time_slots else 50
        slot_minutes = self.data.get("time_config", {}).get("slot_minutes", 30)

        # Precompute valid start slots for each duration
        valid_starts_cache = {}
        if time_slots:
            for c in courses:
                for comp in c.get("components", []):
                    duration = comp.get("duration_slots", 1)
                    if duration in valid_starts_cache:
                        continue
                    valid_starts = []
                    for i in range(0, len(time_slots) - duration + 1):
                        ok = True
                        base_day = time_slots[i]["day"]
                        for k in range(duration - 1):
                            a = time_slots[i + k]
                            b = time_slots[i + k + 1]
                            # ต้องต่อเนื่องกันจริงและอยู่วันเดียวกัน
                            if a["day"] != base_day or b["day"] != base_day:
                                ok = False
                                break
                            if b["start_min"] != a["end_min"]:
                                ok = False
                                break
                        if ok:
                            valid_starts.append(i)
                    valid_starts_cache[duration] = valid_starts

        for c in courses:
            c_id = c["id"]
            components = c.get("components", [])

            self.all_vars[c_id] = {
                "course_id": c_id,
                "activities": {},
            }

            for comp in components:
                act_id = comp["id"]
                duration = comp.get("duration_slots", 1)

                # 1. สร้างตัวแปร Start Time (เริ่มสอนคาบไหน)
                # โดเมนคือ [0, horizon - duration] เพื่อไม่ให้สอนเลยเวลาจบวัน
                if time_slots and duration in valid_starts_cache:
                    valid = valid_starts_cache.get(duration, [])
                    if valid:
                        start_var = self.model.NewIntVarFromDomain(
                            cp_model.Domain.FromValues(valid),
                            f"start_{act_id}",
                        )
                    else:
                        start_var = self.model.NewIntVar(
                            0, horizon - duration, f"start_{act_id}"
                        )
                else:
                    start_var = self.model.NewIntVar(
                        0, horizon - duration, f"start_{act_id}"
                    )
                end_var = self.model.NewIntVar(0, horizon, f"end_{act_id}")

                # สร้าง Interval หลักของกิจกรรมนี้ (ใช้เช็คเวลาครูชนกัน)
                main_interval = self.model.NewIntervalVar(
                    start_var, duration, end_var, f"interval_{act_id}"
                )

                self.all_vars[c_id]["activities"][act_id] = {
                    "start": start_var,
                    "end": end_var,
                    "interval": main_interval,
                    "duration": duration,
                    "type": comp.get("type"),
                    "rooms": {},  # เก็บข้อมูลแยกตามห้อง
                }

                # 2. สร้างตัวแปรเลือกห้อง (Optional Intervals)
                for r in rooms:
                    r_id = r["id"]

                    # ตัวแปร Boolean: กิจกรรมนี้สอนที่ห้องนี้หรือไม่? (1=ใช่, 0=ไม่)
                    is_in_room = self.model.NewBoolVar(f"pres_{act_id}_{r_id}")

                    # ตัวแปร Optional Interval: ช่วงเวลาที่จะเกิดขึ้นจริง ก็ต่อเมื่อ is_in_room = 1
                    # ใช้สำหรับตรวจสอบห้องซ้อนทับกัน (Room Conflict)
                    opt_interval = self.model.NewOptionalIntervalVar(
                        start_var,
                        duration,
                        end_var,
                        is_in_room,
                        f"opt_interval_{act_id}_{r_id}",
                    )

                    self.all_vars[c_id]["activities"][act_id]["rooms"][r_id] = {
                        "is_present": is_in_room,
                        "opt_interval": opt_interval,
                    }

        print(f"Created variables for {len(courses)} courses.")

    def build_model(self):
        self.create_variables()

        # ส่งต่อให้ Constraints Manager
        constraints_manager = Constraints(self.model, self.all_vars, self.data)
        constraints_manager.add_hard_constraints()
        constraints_manager.add_soft_constraints()

        return self.model, self.all_vars
