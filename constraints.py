from ortools.sat.python import cp_model


class Constraints:
    def __init__(self, model, all_vars, data):
        self.model = model
        self.all_vars = all_vars  # Decision Variable
        self.data = data
        self.assumptions = {}
        self.assumption_details = {}

    def add_hard_constraints(self):
        print("Adding Hard Constraints")

        courses = self.data["courses"]
        rooms = self.data["rooms"]
        time_slots = self.data.get("time_slots", [])
        days = self.data.get("time_config", {}).get("days", [])

        # 1) Room No-Overlap:
        # ห้องเดียวกันห้ามมีวิชาซ้อนทับกันในช่วงเวลาเดียวกัน
        for r in rooms:
            r_id = r["id"]
            a_room = self._assumption("room_no_overlap", detail={"room_id": r_id})
            room_intervals = []
            for c in courses:
                c_id = c["id"]
                activities = self.all_vars[c_id]["activities"]
                for act in activities.values():
                    room_intervals.append(act["rooms"][r_id]["opt_interval"])
            if room_intervals:
                self.model.AddNoOverlap(room_intervals).OnlyEnforceIf(a_room)

        # 2) Teacher No-Overlap:
        # อาจารย์คนเดียวกันห้ามสอนหลายวิชาในเวลาเดียวกัน
        all_teachers = self.data.get("teachers", [])
        for teacher in all_teachers:
            teacher_intervals = []
            a_teacher = self._assumption(
                "teacher_no_overlap", detail={"teacher": teacher}
            )
            for c in courses:
                if teacher in c.get("teacher_list", []):
                    c_id = c["id"]
                    activities = self.all_vars[c_id]["activities"]
                    for act_id, act in activities.items():
                        teacher_intervals.append((c_id, act_id, act["interval"]))
            if teacher_intervals:
                # กัน interval ซ้ำใน list ก่อนส่งเข้า AddNoOverlap
                seen = set()
                unique_intervals = []
                for c_id, act_id, interval in teacher_intervals:
                    key = (c_id, act_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    unique_intervals.append(interval)
                self.model.AddNoOverlap(unique_intervals).OnlyEnforceIf(a_teacher)

        # 3) Course Self-Collision:
        # วิชาเดียวกัน (เช่น Lecture กับ Lab) ต้องไม่ซ้อนทับกันเอง
        for c in courses:
            c_id = c["id"]
            a_self = self._assumption("course_self_collision", detail={"course": c_id})
            activities = self.all_vars[c_id]["activities"]
            if len(activities) > 1:
                self.model.AddNoOverlap(
                    [act["interval"] for act in activities.values()]
                ).OnlyEnforceIf(a_self)

        # 4) Day-Bound (Time Slot Continuity):
        # ป้องกันการข้ามวัน โดยบังคับให้ start และ end-1 อยู่วันเดียวกัน
        if time_slots and days:
            day_index = {d: i for i, d in enumerate(days)}
            slot_day_idx = [day_index.get(s["day"], 0) for s in time_slots]
            tuples = [(i, slot_day_idx[i]) for i in range(len(time_slots))]
            a_day = self._assumption("day_bound")
            for c in courses:
                c_id = c["id"]
                activities = self.all_vars[c_id]["activities"]
                for act_id, act in activities.items():
                    start = act["start"]
                    end_minus_1 = self.model.NewIntVar(
                        0, len(time_slots) - 1, f"endm1_{act_id}"
                    )
                    self.model.Add(end_minus_1 == act["end"] - 1).OnlyEnforceIf(
                        a_day
                    )
                    start_day = self.model.NewIntVar(
                        0, len(days) - 1, f"day_s_{act_id}"
                    )
                    end_day = self.model.NewIntVar(
                        0, len(days) - 1, f"day_e_{act_id}"
                    )
                    self.model.AddAllowedAssignments([start, start_day], tuples).OnlyEnforceIf(
                        a_day
                    )
                    self.model.AddAllowedAssignments([end_minus_1, end_day], tuples).OnlyEnforceIf(
                        a_day
                    )
                    self.model.Add(start_day == end_day).OnlyEnforceIf(a_day)

        # 5) Course Completion (L/P):
        # ทุกกิจกรรม (Lecture/Lab) ต้องถูกจัดลงห้องอย่างน้อย 1 ห้อง
        for c in courses:
            c_id = c["id"]
            activities = self.all_vars[c_id]["activities"]
            for act in activities.values():
                a_complete = self._assumption(
                    "course_completion", detail={"activity": act["interval"].Name()}
                )
                self.model.Add(
                    sum(v["is_present"] for v in act["rooms"].values()) == 1
                ).OnlyEnforceIf(a_complete)

        # Register assumptions
        self.model.AddAssumptions(list(self.assumptions.values()))
        self.data["assumption_details"] = self.assumption_details

    def add_soft_constraints(self):
        print("Adding Soft Constraints")

        courses = self.data["courses"]
        rooms = self.data["rooms"]
        time_slots = self.data.get("time_slots", [])
        days = self.data.get("time_config", {}).get("days", [])
        horizon = len(time_slots) if time_slots else 0

        # 1) Capacity Soft Constraint:
        # อนุญาตให้เกินได้เล็กน้อย แต่มี penalty ตามส่วนเกิน (ยิ่งเกินยิ่งโดนลงโทษมาก)
        over_capacity_terms = []
        for c in courses:
            enrollment = self._to_int(c.get("ลง", 0))
            activities = self.all_vars[c["id"]]["activities"]
            for act in activities.values():
                for r in rooms:
                    r_id = r["id"]
                    capacity = self._to_int(r.get("จำนวนที่นั่ง", 0))
                    if capacity and enrollment and enrollment > capacity:
                        over = enrollment - capacity
                        over_capacity_terms.append(
                            over * act["rooms"][r_id]["is_present"]
                        )

        # 2) Balanced Room Usage (Soft):
        # กระจายการใช้ห้องให้สมดุล โดยลด "ความเปลืองความจุ"
        # แนวคิด: ถ้าห้องใหญ่เกินจำนวนลงเรียน ให้มีโทษตามส่วนต่าง (capacity - enrollment)
        penalty_terms = []
        room_usage_counts = {}
        total_activities = 0
        for c in courses:
            c_id = c["id"]
            enrollment = self._to_int(c.get("ลง", 0))
            activities = self.all_vars[c_id]["activities"]
            for act in activities.values():
                total_activities += 1
                for r in rooms:
                    r_id = r["id"]
                    capacity = self._to_int(r.get("จำนวนที่นั่ง", 0))
                    if capacity and enrollment and capacity >= enrollment:
                        waste = capacity - enrollment
                        # โทษเฉพาะห้องที่ถูกเลือก (is_present == 1)
                        penalty_terms.append(waste * act["rooms"][r_id]["is_present"])
                    # เก็บตัวแปรการใช้ห้องเพื่อทำสมดุล
                    room_usage_counts.setdefault(r_id, []).append(
                        act["rooms"][r_id]["is_present"]
                    )

        # 3) Balanced Room Usage Count (Soft):
        # ลดความต่างของจำนวนครั้งที่ใช้ห้อง (ไม่ให้ห้องใดถูกใช้มากเกินไป)
        balance_terms = []
        if rooms and total_activities > 0:
            max_usage = self.model.NewIntVar(0, total_activities, "max_room_usage")
            min_usage = self.model.NewIntVar(0, total_activities, "min_room_usage")

            for r in rooms:
                r_id = r["id"]
                usage_list = room_usage_counts.get(r_id, [])
                if usage_list:
                    usage = self.model.NewIntVar(
                        0, total_activities, f"room_usage_{r_id}"
                    )
                    self.model.Add(usage == sum(usage_list))
                    self.model.Add(max_usage >= usage)
                    self.model.Add(min_usage <= usage)

            # เป้าหมาย: ลดช่องว่างระหว่างห้องที่ใช้มากที่สุดกับน้อยที่สุด
            balance_terms.append(max_usage - min_usage)

        # 4) Balanced Day Usage + Compactness (Soft):
        # กระจายตารางให้เหมาะสมทั้งสัปดาห์ และในแต่ละวันให้กระชับ
        day_balance_terms = []
        day_compact_terms = []
        if time_slots and days:
            # map slot -> day index
            slot_day_idx = []
            day_index = {d: i for i, d in enumerate(days)}
            for s in time_slots:
                slot_day_idx.append(day_index.get(s["day"], 0))

            # สร้างตัวแปร day สำหรับแต่ละ activity และนับจำนวนกิจกรรมต่อวัน
            day_counts = [
                self.model.NewIntVar(0, len(courses) * 2, f"day_count_{d}")
                for d in days
            ]
            day_bools = [[] for _ in days]

            for c in courses:
                c_id = c["id"]
                for act_id, act in self.all_vars[c_id]["activities"].items():
                    start = act["start"]
                    day_var = self.model.NewIntVar(0, len(days) - 1, f"day_{act_id}")
                    # table mapping: (start_slot, day_idx)
                    tuples = [(i, slot_day_idx[i]) for i in range(len(time_slots))]
                    self.model.AddAllowedAssignments([start, day_var], tuples)

                    for d_idx, _ in enumerate(days):
                        b = self.model.NewBoolVar(f"act_{act_id}_is_day_{d_idx}")
                        self.model.Add(day_var == d_idx).OnlyEnforceIf(b)
                        self.model.Add(day_var != d_idx).OnlyEnforceIf(b.Not())
                        day_bools[d_idx].append((b, act))

            # นับจำนวนกิจกรรมต่อวัน
            for d_idx in range(len(days)):
                if day_bools[d_idx]:
                    self.model.Add(
                        day_counts[d_idx] == sum(b for b, _ in day_bools[d_idx])
                    )
                else:
                    self.model.Add(day_counts[d_idx] == 0)

            # สมดุลรายวัน: ลดช่องว่าง max-min ระหว่างวัน
            max_day = self.model.NewIntVar(0, len(courses) * 2, "max_day_usage")
            min_day = self.model.NewIntVar(0, len(courses) * 2, "min_day_usage")
            for d_idx in range(len(days)):
                self.model.Add(max_day >= day_counts[d_idx])
                self.model.Add(min_day <= day_counts[d_idx])
            day_balance_terms.append(max_day - min_day)

            # กระชับในแต่ละวัน: ลดช่วงเวลา (max_end - min_start)
            if horizon > 0:
                for d_idx, d in enumerate(days):
                    bools_acts = day_bools[d_idx]
                    if not bools_acts:
                        continue

                    # สร้างตัวแปร start_if_day และ end_if_day
                    start_if_list = []
                    end_if_list = []
                    for b, act in bools_acts:
                        start_if = self.model.NewIntVar(
                            0, horizon, f"start_if_{act['interval'].Name()}_{d}"
                        )
                        end_if = self.model.NewIntVar(
                            0, horizon, f"end_if_{act['interval'].Name()}_{d}"
                        )
                        self.model.Add(start_if == act["start"]).OnlyEnforceIf(b)
                        self.model.Add(start_if == horizon).OnlyEnforceIf(b.Not())
                        self.model.Add(end_if == act["end"]).OnlyEnforceIf(b)
                        self.model.Add(end_if == 0).OnlyEnforceIf(b.Not())
                        start_if_list.append(start_if)
                        end_if_list.append(end_if)

                    has_day = self.model.NewBoolVar(f"has_day_{d}")
                    self.model.Add(sum(b for b, _ in bools_acts) >= 1).OnlyEnforceIf(
                        has_day
                    )
                    self.model.Add(sum(b for b, _ in bools_acts) == 0).OnlyEnforceIf(
                        has_day.Not()
                    )

                    start_dummy = self.model.NewIntVar(0, horizon, f"start_dummy_{d}")
                    self.model.Add(start_dummy == horizon).OnlyEnforceIf(has_day)
                    self.model.Add(start_dummy == 0).OnlyEnforceIf(has_day.Not())
                    start_if_list.append(start_dummy)

                    min_start = self.model.NewIntVar(0, horizon, f"min_start_{d}")
                    max_end = self.model.NewIntVar(0, horizon, f"max_end_{d}")
                    self.model.AddMinEquality(min_start, start_if_list)
                    self.model.AddMaxEquality(max_end, end_if_list)

                    span = self.model.NewIntVar(0, horizon, f"day_span_{d}")
                    self.model.Add(span == max_end - min_start)
                    day_compact_terms.append(span)

        # 5) Same Room for Same Subject + Type (Soft):
        # รายวิชาเดียวกัน (ตามรหัสวิชา) และประเภทเดียวกัน ควรใช้ห้องเดียวกัน
        same_room_terms = []
        subject_groups = {}
        for c in courses:
            subject_code = str(c.get("รหัสวิชา", "")).strip()
            if not subject_code:
                continue
            c_id = c["id"]
            for act_id, act in self.all_vars[c_id]["activities"].items():
                act_type = act.get("type")
                key = (subject_code, act_type)
                subject_groups.setdefault(key, []).append(act)

        for (subject_code, act_type), acts in subject_groups.items():
            if len(acts) <= 1:
                continue
            used_rooms = []
            for r in rooms:
                r_id = r["id"]
                bools = [a["rooms"][r_id]["is_present"] for a in acts]
                used = self.model.NewBoolVar(f"used_{subject_code}_{act_type}_{r_id}")
                self.model.AddMaxEquality(used, bools)
                used_rooms.append(used)
            # ลดจำนวนห้องที่ถูกใช้ในกลุ่มนี้
            if used_rooms:
                extra_rooms = self.model.NewIntVar(
                    0, len(rooms), f"extra_rooms_{subject_code}_{act_type}"
                )
                self.model.Add(extra_rooms == sum(used_rooms) - 1)
                same_room_terms.append(extra_rooms)

        # 6) Teacher Unavailability (Hard) - COMMENT ONLY:
        # วิธีใช้: หากมีข้อมูลเวลาที่อาจารย์ไม่ว่าง ให้สร้าง allowed slots ของแต่ละอาจารย์
        # แล้วบังคับให้ start ของกิจกรรมอยู่ใน allowed slots นั้น
        # ตัวอย่าง:
        #   unavailable = {"Teacher A": [slot_index1, slot_index2, ...]}
        #   allowed = [s for s in range(horizon) if s not in unavailable["Teacher A"]]
        #   model.AddAllowedAssignments([act["start"]], [(s,) for s in allowed])

        # 7) Room Type Constraint (Hard) - COMMENT ONLY:
        # วิธีใช้: หากมีข้อมูลประเภทห้อง เช่น "LAB", "LECTURE"
        # ให้กำหนดประเภทห้องใน data_loader แล้วบังคับให้กิจกรรมเลือกห้องที่ type ตรงกัน
        # ตัวอย่าง:
        #   if act["type"] == "P" (Lab) ให้ปิดห้องที่ไม่ใช่ LAB:
        #   model.Add(act["rooms"][r_id]["is_present"] == 0)  # ถ้า r.type != "LAB"

        # รวม Soft Constraints เป็น Objective เดียว (Weighted Sum)
        if (
            over_capacity_terms
            or penalty_terms
            or balance_terms
            or day_balance_terms
            or day_compact_terms
            or same_room_terms
        ):
            weight_capacity = 1
            weight_over_capacity = 5
            weight_balance = 10
            weight_day_balance = 5
            weight_day_compact = 1
            weight_same_room = 3
            objective = []
            if over_capacity_terms:
                objective.append(weight_over_capacity * sum(over_capacity_terms))
            if penalty_terms:
                objective.append(weight_capacity * sum(penalty_terms))
            if balance_terms:
                objective.append(weight_balance * sum(balance_terms))
            if day_balance_terms:
                objective.append(weight_day_balance * sum(day_balance_terms))
            if day_compact_terms:
                objective.append(weight_day_compact * sum(day_compact_terms))
            if same_room_terms:
                objective.append(weight_same_room * sum(same_room_terms))
            self.model.Minimize(sum(objective))

    def _to_int(self, value):
        if value is None:
            return 0
        try:
            return int(str(value).strip())
        except ValueError:
            # กรณีมีตัวอักษรปน เช่น "360I"
            digits = "".join([c for c in str(value) if c.isdigit()])
            return int(digits) if digits else 0

    def _assumption(self, name, detail=None):
        key = name
        if detail:
            # สร้าง key ที่บอกบริบทเพื่อแยกกรณี
            detail_tag = "_".join(
                f"{k}:{str(v)}" for k, v in detail.items() if v is not None
            )
            key = f"{name}__{detail_tag}"
        if key in self.assumptions:
            return self.assumptions[key]
        a = self.model.NewBoolVar(f"assump_{key}")
        self.assumptions[key] = a
        if detail:
            self.assumption_details[a.Name()] = {"type": name, **detail}
        return a
