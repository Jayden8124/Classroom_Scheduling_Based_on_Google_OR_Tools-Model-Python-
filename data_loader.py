import pandas as pd
import os
import re
import difflib


class DataLoader:
    def __init__(self, data_dir):
        # Variable for dataset
        self.data_dir = data_dir  # path data directory
        self.courses = []
        self.rooms = []
        self.all_teachers = (
            set()
        )  # เก็บรายชื่ออาจารย์ทั้งหมด (ไม่ซ้ำ) เพื่อใช้ตอนวน Loop สร้าง Constraint
        self.teacher_aliases = {}  # เก็บ mapping ชื่อเดิม -> ชื่อมาตรฐาน (dedupe)
        self.teacher_typos = []  # เก็บรายการชื่อที่สงสัยว่าเป็นการพิมพ์ผิด

        # Constant Data สำหรับวันเรียน (จันทร์ - ศุกร์)
        self.days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

        # Time config (ใช้สำหรับสร้าง Time Slots)
        self.slot_minutes = 30
        self.day_start = "08:30"
        self.day_end = "17:00"
        self.lunch_start = "12:00"
        self.lunch_end = "13:00"

        # ถ้าไม่มีคอลัมน์ "ประเภท" สามารถกำหนด rule เองได้ที่นี่
        # ตัวอย่าง: {"default": {"lecture_sections": [1, 2], "lab_sections": [3, 4]}}
        self.section_type_rules = {}

    def load_data(self):
        print("--- Loading Data ---")

        # Load Courses
        courses_path = os.path.join(self.data_dir, "Comsci_Test.csv")
        if os.path.exists(courses_path):
            df_courses = pd.read_csv(courses_path, dtype=str)

            print(f"\n[Preview] Raw Courses Data:")
            print(df_courses.head())

            # เรียกใช้ฟังก์ชันประมวลผลข้อมูล (สร้าง ID และ แยกชื่ออาจารย์)
            self.courses = self._process_courses(df_courses)
        else:
            print(f"Error: File not found at {courses_path}")

        # Load Rooms
        rooms_path = os.path.join(self.data_dir, "Room.csv")
        if os.path.exists(rooms_path):
            df_rooms = pd.read_csv(rooms_path, dtype=str)

            print(f"\n[Preview] Rooms ({rooms_path}):")
            print(df_rooms.head())

            # สร้าง Room ID และเก็บข้อมูล
            self.rooms = []
            for idx, row in df_rooms.iterrows():
                # ใช้ อาคาร + ห้อง เพื่อให้ไม่ซ้ำ (เช่น SC08_201)
                building = str(row.get("อาคาร", "")).strip()
                room_no = str(row.get("ห้อง", f"room_{idx}")).strip()
                r_id = f"{building}_{room_no}" if building else room_no

                # แปลงข้อมูล Room เป็น dict
                room_data = row.to_dict()
                room_data["id"] = r_id
                self.rooms.append(room_data)

            print(f"Loaded {len(self.rooms)} rooms.")
        else:
            print(f"Error: File not found at {rooms_path}")

        # Generate Time Slots
        time_slots = self._generate_time_slots()

        return {
            "courses": self.courses,
            "rooms": self.rooms,
            "teachers": list(self.all_teachers),
            "time_slots": time_slots,
            "course_catalog": self._build_course_catalog(self.courses),
            "time_config": {
                "slot_minutes": self.slot_minutes,
                "day_start": self.day_start,
                "day_end": self.day_end,
                "lunch_start": self.lunch_start,
                "lunch_end": self.lunch_end,
                "days": self.days,
            },
        }

    def _process_courses(self, df):
        """
        สร้าง UID ให้แต่ละวิชา โดย format: {รหัสวิชา}_{กลุ่มเรียน}
        และแยกรายชื่ออาจารย์ออกจาก string
        """
        processed_data = []

        lps_column = self._find_lps_column(df.columns)
        type_column = self._find_type_column(df.columns)
        pair_column = self._find_pair_column(df.columns)
        type_index = self._build_type_index(df, type_column)

        for index, row in df.iterrows():
            # 1. สร้าง UID (Unique ID)
            # ดึงรหัสวิชา และ กลุ่มเรียน (Section) และ ชั้นปี
            # หมายเหตุ: ใช้ชื่อคอลัมน์ 'รหัสวิชา' และ 'กลุ่มเรียน' ตาม CSV
            subject_code = str(row.get("รหัสวิชา", "")).strip()
            section = str(row.get("กลุ่มเรียน", "")).strip()
            year = str(row.get("ชั้นปี", "")).strip()

            # Fallback: ถ้าหา 'กลุ่มเรียน' ไม่เจอ ให้ลองหา 'Section' หรือตั้งค่า default
            if not section:
                section = str(row.get("Section", "1")).strip()

            # สร้าง UID เช่น 05506003_1_Y1, 05506003_2_Y1
            if subject_code:
                uid = f"{subject_code}_{section}"
                if year:
                    uid = f"{uid}_Y{year}"
            else:
                uid = f"unknown_{index}"

            # 2. จัดการรายชื่ออาจารย์ (Teacher List)
            teacher_str = str(row.get("อาจารย์ผู้สอน", ""))
            teacher_list = []

            if teacher_str and teacher_str.lower() != "nan":
                # แปลงตัวคั่น (Delimiter) ให้เป็นมาตรฐาน (เผื่อมีทั้ง , และ /)
                teacher_str = teacher_str.replace("/", ",").replace(";", ",")

                # แยก string เป็น list และลบช่องว่าง
                parts = teacher_str.split(",")
                for t in parts:
                    t_name = t.strip()
                    if t_name:
                        canonical, typo_info = self._dedupe_teacher_name(t_name)
                        teacher_list.append(canonical)
                        self.all_teachers.add(canonical)  # เก็บลง Set รวม
                        if typo_info:
                            self.teacher_typos.append(typo_info)

            # แปลง Row เป็น Dict และเพิ่ม field ใหม่เข้าไป
            course_dict = row.to_dict()
            course_dict["id"] = uid  # ใช้ key 'id' เป็นหลักสำหรับ Solver
            course_dict["uid"] = uid  # เก็บ key 'uid' ไว้ด้วยเพื่อความชัดเจน
            course_dict["teacher_list"] = teacher_list
            l_hours, p_hours, s_hours = self._extract_lps(row, lps_column)
            type_hint = self._extract_type_hint(
                row, type_column, pair_column, type_index, subject_code
            )
            course_dict["l_hours"] = l_hours
            course_dict["p_hours"] = p_hours
            course_dict["s_hours"] = s_hours
            course_dict["type_hint"] = type_hint
            course_dict["components"] = self._build_components(
                uid, l_hours, p_hours, type_hint
            )

            processed_data.append(course_dict)

        print(
            f"\n[Processed] Generated IDs and Teacher Lists for {len(processed_data)} courses."
        )
        # ให้ผู้ใช้เลือกตัดรหัสวิชาออกทาง terminal (ชั่วคราวแทน UI)
        processed_data = self._apply_exclusions(processed_data)

        # ===== Preview Processed Data (Head 10) =====
        print("\n[Preview] Processed Courses (uid + teacher_list) [Head 10]:")
        for c in processed_data[:20]:
            print(
                {
                    "uid": c.get("uid"),
                    "subject": c.get("รหัสวิชา"),
                    "section": c.get("กลุ่มเรียน"),  # แสดงผลกลุ่มเรียนด้วย
                    "teacher_list": c.get("teacher_list"),
                    "type": c.get("type_hint"),
                    "l_p_s": (c.get("l_hours"), c.get("p_hours"), c.get("s_hours")),
                    "components": [
                        (x.get("id"), x.get("type"), x.get("duration_slots"))
                        for x in c.get("components", [])
                    ],
                }
            )

        # ===== Preview Teachers (Unique + Possible Typos) =====
        print("\n[Preview] Unique Teachers (Count):", len(self.all_teachers))
        print("[Preview] Unique Teachers (Sample 10):", list(self.all_teachers)[:22])

        if self.teacher_typos:
            print("\n[Warning] Possible Teacher Name Typos:")
            for item in self.teacher_typos[:20]:
                print(item)

        return processed_data

    def _apply_exclusions(self, courses):
        """
        ให้ผู้ใช้กรอกรหัสวิชาที่ต้องการตัดออกทาง terminal
        รองรับการพิมพ์หลายรหัสคั่นด้วยคอมม่า
        """
        course_catalog = self._build_course_catalog(courses)

        # แสดงรายการวิชาแบบย่อเพื่อใช้ตัดสินใจ (เหมาะกับ UI ในอนาคต)
        print("\n[Course Catalog] (Sample 20):")
        for item in course_catalog[:20]:
            print(item)

        # แสดงรายวิชาที่ L = 0 เพื่อช่วยตัดสินใจ
        zero_l = [
            {
                "subject": c.get("รหัสวิชา"),
                "name": c.get("ชื่อวิชาภาษาอังกฤษ"),
                "section": c.get("กลุ่มเรียน"),
                "l_p_s": (c.get("l_hours"), c.get("p_hours"), c.get("s_hours")),
                "type": c.get("type_hint"),
            }
            for c in courses
            if c.get("l_hours", 0) == 0
        ]
        if zero_l:
            print("\n[Hint] Courses with L = 0 (Sample 10):")
            for item in zero_l[:10]:
                print(item)

        raw = input(
            "\nEnter course codes to exclude (comma-separated), or press Enter to skip: "
        ).strip()
        if not raw:
            return courses

        exclude_codes = {x.strip() for x in raw.split(",") if x.strip()}
        if not exclude_codes:
            return courses

        filtered = []
        removed = []
        for c in courses:
            code = str(c.get("รหัสวิชา", "")).strip()
            if code in exclude_codes:
                removed.append(
                    {
                        "uid": c.get("uid"),
                        "subject": code,
                        "name": c.get("ชื่อวิชาภาษาอังกฤษ"),
                        "section": c.get("กลุ่มเรียน"),
                        "l_p_s": (c.get("l_hours"), c.get("p_hours"), c.get("s_hours")),
                        "type": c.get("type_hint"),
                    }
                )
            else:
                filtered.append(c)

        if removed:
            print("\n[Filtered] Courses Removed (By User Selection):")
            for item in removed:
                print(item)

        return filtered

    def _build_course_catalog(self, courses):
        """
        สร้างรายการวิชาสำหรับแสดงใน UI (หรือ terminal ชั่วคราว)
        """
        seen = set()
        catalog = []
        for c in courses:
            code = str(c.get("รหัสวิชา", "")).strip()
            name = str(c.get("ชื่อวิชาภาษาอังกฤษ", "")).strip()
            if not code:
                continue
            key = (code, name)
            if key in seen:
                continue
            seen.add(key)
            catalog.append(
                {
                    "code": code,
                    "name": name,
                }
            )
        return catalog

    def _find_lps_column(self, columns):
        """
        หา column ที่เก็บค่า L-P-S หากมี
        """
        lps_candidates = [
            "L-P-S",
            "L/P/S",
            "LPS",
            "L-P-S (หน่วย)",
            "L-P-S (units)",
            "L-P-S หน่วย",
            "(L-P-S)",
        ]

        for col in columns:
            col_str = str(col).strip()
            if col_str in lps_candidates:
                return col_str

        # ถ้าไม่เจอ ลองหาแบบมีตัวอักษร L/P/S อยู่ในชื่อ
        for col in columns:
            col_str = str(col).strip().upper()
            if "L" in col_str and "P" in col_str and "S" in col_str:
                return str(col).strip()

        return None

    def _find_type_column(self, columns):
        """
        หา column ที่เก็บค่า "ประเภท" (ทฤษฎี/ปฏิบัติ)
        """
        candidates = ["ประเภท", "Type", "Course Type", "Lecture/Lab", "L/P"]
        for col in columns:
            col_str = str(col).strip()
            if col_str in candidates:
                return col_str
        return None

    def _find_pair_column(self, columns):
        """
        หา column ที่เก็บค่า "กลุ่มจับคู่"
        """
        candidates = ["กลุ่มจับคู่", "Pair", "Paired Section", "คู่กลุ่ม"]
        for col in columns:
            col_str = str(col).strip()
            if col_str in candidates:
                return col_str
        return None

    def _build_type_index(self, df, type_column):
        """
        สร้าง index: (รหัสวิชา, กลุ่มเรียน) -> ประเภท(L/P)
        ใช้สำหรับอนุมานจากกลุ่มจับคู่
        """
        index = {}
        if not type_column:
            return index

        for _, row in df.iterrows():
            subject_code = str(row.get("รหัสวิชา", "")).strip()
            section = str(row.get("กลุ่มเรียน", "")).strip()
            t = self._normalize_type(str(row.get(type_column, "")).strip())
            if subject_code and section and t:
                index[(subject_code, section)] = t

        return index

    def _extract_lps(self, row, lps_column):
        """
        Extract L-P-S จากแถวข้อมูล
        รองรับทั้งรูปแบบ 2-1-3 หรือมีคอลัมน์ L, P, S แยก
        """
        # 1) ถ้ามีคอลัมน์ L, P, S แยก
        l_val = row.get("L", None)
        p_val = row.get("P", None)
        s_val = row.get("S", None)
        if l_val is not None or p_val is not None or s_val is not None:
            return self._to_int(l_val), self._to_int(p_val), self._to_int(s_val)

        # 2) ถ้ามีคอลัมน์ L-P-S แบบรวม
        if lps_column:
            raw = str(row.get(lps_column, "")).strip()
            nums = re.findall(r"\d+", raw)
            if len(nums) >= 3:
                return int(nums[0]), int(nums[1]), int(nums[2])
            if len(nums) == 2:
                return int(nums[0]), int(nums[1]), 0

        return 0, 0, 0

    def _extract_type_hint(
        self, row, type_column, pair_column, type_index, subject_code
    ):
        """
        แปลงค่าประเภทให้เป็น L หรือ P
        """
        raw = ""
        if type_column:
            raw = str(row.get(type_column, "")).strip()
        t = self._normalize_type(raw)
        if t:
            return t

        # ถ้าไม่มีประเภท ให้อนุมานจาก "กลุ่มจับคู่"
        if pair_column:
            pair_section = str(row.get(pair_column, "")).strip()
            if subject_code and pair_section:
                paired_type = type_index.get((subject_code, pair_section))
                if paired_type == "L":
                    return "P"
                if paired_type == "P":
                    return "L"

        # ถ้าไม่มีประเภท ให้ลองอนุมานจากกลุ่มเรียนด้วย rule (ถ้ามี)
        section = str(row.get("กลุ่มเรียน", "")).strip()
        if section and self.section_type_rules.get("default"):
            try:
                section_num = int(section)
                rule = self.section_type_rules["default"]
                if section_num in rule.get("lecture_sections", []):
                    return "L"
                if section_num in rule.get("lab_sections", []):
                    return "P"
            except ValueError:
                pass

        return None

    def _normalize_type(self, raw):
        raw_lower = raw.lower()
        if "ทฤษฎี" in raw or "lecture" in raw_lower or raw_lower == "l":
            return "L"
        if "ปฏิบัติ" in raw or "lab" in raw_lower or raw_lower == "p":
            return "P"
        return None

    def _normalize_teacher_name(self, name):
        """
        ทำ normalization เพื่อลดปัญหาชื่อซ้ำ/พิมพ์ผิดเล็กน้อย
        """
        n = name.strip()
        n = n.replace("\u200b", "")  # zero-width space
        n = re.sub(r"\s+", " ", n)
        n = n.replace(".", "").replace(",", "")
        return n

    def _dedupe_teacher_name(self, name):
        """
        คืนค่าชื่อมาตรฐาน (canonical) และข้อมูล typo ถ้ามี
        ใช้ similarity แบบง่ายเพื่อจับพิมพ์ผิดเล็กน้อย
        """
        cleaned = self._normalize_teacher_name(name)
        key = cleaned.lower().replace(" ", "")

        # ถ้าเคยเจอแล้ว ใช้ชื่อมาตรฐานเดิม
        if key in self.teacher_aliases:
            return self.teacher_aliases[key], None

        # ลองหาใกล้เคียงในชื่อที่มีอยู่
        for existing in self.all_teachers:
            exist_key = self._normalize_teacher_name(existing).lower().replace(" ", "")
            ratio = difflib.SequenceMatcher(None, key, exist_key).ratio()
            if ratio >= 0.9 and key != exist_key:
                # ถือว่าเป็น typo เล็กน้อย
                self.teacher_aliases[key] = existing
                return (
                    existing,
                    {
                        "raw": name,
                        "canonical": existing,
                        "similarity": round(ratio, 3),
                        "diff": self._simple_diff(existing, name),
                    },
                )

        # ถ้าไม่เจอใกล้เคียง ให้ใช้ชื่อที่ normalize แล้วเป็น canonical
        self.teacher_aliases[key] = cleaned
        return cleaned, None

    def _simple_diff(self, canonical, raw):
        """
        สรุปความต่างแบบอ่านง่าย (ใช้เพื่อ preview)
        """
        return {
            "canonical": canonical,
            "raw": raw,
        }

    def _build_components(self, uid, l_hours, p_hours, type_hint=None):
        """
        สร้าง component ย่อยของรายวิชา (Lecture/Practice)
        ใช้ L และ P เท่านั้นตาม requirement
        """
        components = []
        if type_hint == "L" and l_hours and l_hours > 0:
            components.append(
                {
                    "id": f"{uid}_L",
                    "type": "L",
                    "hours": l_hours,
                    "duration_slots": self._hours_to_slots(l_hours),
                }
            )
        elif type_hint == "P" and p_hours and p_hours > 0:
            components.append(
                {
                    "id": f"{uid}_P",
                    "type": "P",
                    "hours": p_hours,
                    "duration_slots": self._hours_to_slots(p_hours),
                }
            )
        elif type_hint is None:
            # ถ้าไม่มีประเภท ให้สร้างทั้ง L และ P
            if l_hours and l_hours > 0:
                components.append(
                    {
                        "id": f"{uid}_L",
                        "type": "L",
                        "hours": l_hours,
                        "duration_slots": self._hours_to_slots(l_hours),
                    }
                )
            if p_hours and p_hours > 0:
                components.append(
                    {
                        "id": f"{uid}_P",
                        "type": "P",
                        "hours": p_hours,
                        "duration_slots": self._hours_to_slots(p_hours),
                    }
                )
        return components

    def _hours_to_slots(self, hours):
        return int((hours * 60) / self.slot_minutes)

    def _to_int(self, value):
        if value is None:
            return 0
        try:
            return int(str(value).strip())
        except ValueError:
            return 0

    def _generate_time_slots(self):
        """
        สร้าง Time Slots ตามเงื่อนไข:
        เรียน 08:30 - 17:00 พักเที่ยง 12:00 - 13:00
        """
        slots = []

        start_min = self._time_to_minutes(self.day_start)
        end_min = self._time_to_minutes(self.day_end)
        lunch_start = self._time_to_minutes(self.lunch_start)
        lunch_end = self._time_to_minutes(self.lunch_end)

        for day in self.days:
            current = start_min
            while current + self.slot_minutes <= end_min:
                next_t = current + self.slot_minutes

                # Skip lunch break
                if not (current >= lunch_start and next_t <= lunch_end):
                    slot_label = f"{day} {self._minutes_to_time(current)}-{self._minutes_to_time(next_t)}"
                    slots.append(
                        {
                            "day": day,
                            "start_min": current,
                            "end_min": next_t,
                            "label": slot_label,
                        }
                    )

                current = next_t

        print(f"\n[Generated] Time Slots: {len(slots)} slots")
        print("[Preview] Time Slots (Head 10):")
        for s in slots[:10]:
            print(s)

        return slots

    def _time_to_minutes(self, hhmm):
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    def _minutes_to_time(self, minutes):
        h = minutes // 60
        m = minutes % 60
        return f"{h:02d}:{m:02d}"
