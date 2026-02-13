import sys
import os

# เพิ่ม path เพื่อให้ import modules ได้สะดวก
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.data_loader import DataLoader
from src.model import TimetableModel
from src.solver import TimetableSolver
from datetime import datetime

"""
    Main execution flow:
    1. Load Data
    2. Build Model
    3. Solve & Export
    """


def main_program():
    # === Display Start Time Program ===
    start_time = datetime.now()
    print("\n================ PROGRAM STARTED ================")
    print("Start Time:", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=================================================\n")

    # Setup paths & Load Data
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    data = DataLoader(data_dir).load_data()

    # Check Data Loaded
    if not data["courses"] and not data["rooms"]:
        print("Error: No data loaded. Exiting.")
        return

    # Initialize Model
    timetable_model = TimetableModel(data)

    # Build Model
    model, all_vars = timetable_model.build_model()

    # Solve & Output
    solver = TimetableSolver(model, all_vars, data)
    solver.solve()

    # === Display End Time Program ===
    end_time = datetime.now()
    print("\n================ PROGRAM ENDED ================")
    print("Program Finished at:", end_time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Total Elapsed:", (end_time - start_time))
    print("====================================================\n")


if __name__ == "__main__":
    main_program()
