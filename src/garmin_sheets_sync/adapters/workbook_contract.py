"""Workbook schema owned by spreadsheet destination adapters."""

WEIGHT_HEADERS = (
    "Measurement Timestamp",
    "Weight (kg)",
    "Body Fat (%)",
    "Skeletal Muscle Mass (kg)",
    "Bone Mass (kg)",
    "Body Water (%)",
    "BMI",
    "Source",
)
DAILY_HEADERS = ("Date", "Steps", "Active Calories")
ACTIVITY_HEADERS = (
    "Activity ID",
    "Activity Name",
    "Activity Type",
    "Start Time",
    "Duration (seconds)",
    "Distance (meters)",
    "Calories (kcal)",
    "Average Heart Rate (bpm)",
    "Max Heart Rate (bpm)",
    "Garmin Connect Link",
)

# The user's Excel template stores its managed ranges as named tables beginning
# on row 3. These constants intentionally describe only the columns owned by the
# sync; formula and manually maintained columns remain workbook-owned.
EXCEL_WEIGHT_TABLE = "WeightLog"
EXCEL_WEIGHT_HEADERS = (
    "Date",
    "Timestamp",
    "Weight (kg)",
    "Body Fat (%)",
    "Muscle Mass (lb)",
    "Bone Mass (lb)",
    "Body Water (%)",
    "BMI",
    "Source",
    "Sync Timestamp",
)
EXCEL_WEIGHT_FORMULA_HEADERS = (
    "Weight (lb)",
    "7-Day Avg (lb)",
    "Goal Min (lb)",
    "Goal Max (lb)",
    "Pace Slow (lb)",
    "Pace Fast (lb)",
    "Band Lower (fast edge)",
    "Band Height",
)

EXCEL_DAILY_TABLE = "GarminDaily"
EXCEL_DAILY_HEADERS = ("Date", "Steps", "Active Calories")

EXCEL_ACTIVITY_TABLE = "GarminActivities"
EXCEL_ACTIVITY_HEADERS = (
    "Date",
    "Activity Type",
    "Activity Name",
    "Duration",
    "Distance",
    "Start Time",
    "Active Calories",
    "Garmin Activity ID",
    "Garmin Connect Link",
)
