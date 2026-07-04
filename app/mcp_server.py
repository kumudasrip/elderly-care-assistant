import os
import json
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("Elderly Care Service")

# Paths for JSON databases (stored locally in the project directory)
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DB_DIR, exist_ok=True)

MEDS_FILE = os.path.join(DB_DIR, "medications.json")
APPOINTMENTS_FILE = os.path.join(DB_DIR, "appointments.json")
WELLBEING_FILE = os.path.join(DB_DIR, "wellbeing.json")

def _read_json(filepath: str) -> list:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _write_json(filepath: str, data: list):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

@mcp.tool()
def add_medication(name: str, dosage: str, time_of_day: str) -> str:
    """Adds a medication to the schedule.

    Args:
        name: The name of the medication.
        dosage: The dosage amount (e.g., '10mg', '1 tablet').
        time_of_day: When it should be taken (e.g., '8:00 AM', 'before bed').
    """
    meds = _read_json(MEDS_FILE)
    meds.append({
        "name": name,
        "dosage": dosage,
        "time_of_day": time_of_day
    })
    _write_json(MEDS_FILE, meds)
    return f"Success: Scheduled {name} ({dosage}) at {time_of_day}."

@mcp.tool()
def get_medications() -> str:
    """Retrieves all scheduled medications."""
    meds = _read_json(MEDS_FILE)
    if not meds:
        return "No medications are currently scheduled."
    lines = [f"- {m['name']} ({m['dosage']}) at {m['time_of_day']}" for m in meds]
    return "Current Medication Schedule:\n" + "\n".join(lines)

@mcp.tool()
def schedule_appointment(doctor: str, datetime_str: str, reason: str) -> str:
    """Schedules a new doctor appointment.

    Args:
        doctor: The doctor's name or clinic.
        datetime_str: Date and time of the appointment.
        reason: Reason for the visit.
    """
    appointments = _read_json(APPOINTMENTS_FILE)
    appointments.append({
        "doctor": doctor,
        "datetime": datetime_str,
        "reason": reason
    })
    _write_json(APPOINTMENTS_FILE, appointments)
    return f"Success: Appointment scheduled with Dr. {doctor} on {datetime_str} for {reason}."

@mcp.tool()
def get_appointments() -> str:
    """Retrieves all scheduled doctor appointments."""
    appointments = _read_json(APPOINTMENTS_FILE)
    if not appointments:
        return "No doctor appointments are currently scheduled."
    lines = [f"- Dr. {a['doctor']} on {a['datetime']} (Reason: {a['reason']})" for a in appointments]
    return "Scheduled Appointments:\n" + "\n".join(lines)

@mcp.tool()
def log_wellbeing(mood: str, symptoms: str, sleep_hours: float) -> str:
    """Logs the patient's daily well-being status.

    Args:
        mood: The patient's general mood (e.g., 'Happy', 'Anxious', 'Tired').
        symptoms: Any noted symptoms or complaints (e.g., 'None', 'Mild headache').
        sleep_hours: Hours of sleep last night.
    """
    logs = _read_json(WELLBEING_FILE)
    logs.append({
        "mood": mood,
        "symptoms": symptoms,
        "sleep_hours": sleep_hours
    })
    _write_json(WELLBEING_FILE, logs)
    return f"Success: Logged daily well-being (Mood: {mood}, Symptoms: {symptoms}, Sleep: {sleep_hours} hrs)."

if __name__ == "__main__":
    mcp.run(transport="stdio")
