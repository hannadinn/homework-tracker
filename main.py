"""
Homework Tracker - main FastAPI application.

Routes:
  GET  /                                    -> web app shell
  GET  /manifest.json                        -> PWA manifest

  GET  /classes                               -> list existing class sheets
  POST /classes                                -> create a class sheet from an uploaded CSV

  GET  /classes/{class_name}/assignments        -> list assignment columns
  POST /classes/{class_name}/assignments         -> create a new assignment column

  POST /classes/{class_name}/instruct             -> send a chat instruction, scoped to a class

Run locally:
    uvicorn main:app --reload

Deploy to Cloud Run:
    gcloud run deploy homework-tracker --source . --region us-central1 --allow-unauthenticated
"""

import csv
import io
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
if not ENV_PATH.exists():
    print(f"[main] WARNING: no .env file found at {ENV_PATH}")
load_dotenv(dotenv_path=ENV_PATH)

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import call_agent
from sheets_client import (
    class_exists,
    create_assignment,
    create_class,
    get_assignments,
    list_classes,
)

app = FastAPI(title="Homework Tracker")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse("static/manifest.json")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Class list
# ---------------------------------------------------------------------------

@app.get("/classes")
def get_classes():
    return {"classes": list_classes()}


@app.post("/classes")
async def post_class(file: UploadFile):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Please upload a .csv file.")

    class_name = os.path.splitext(file.filename)[0].strip()
    if not class_name:
        raise HTTPException(400, "Could not determine a class name from the filename.")
    if class_exists(class_name):
        raise HTTPException(400, f"A class named '{class_name}' already exists.")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "Could not read the file as UTF-8 text.")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "Name" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have a 'Name' column as the first column.")

    # Only the Name column is used -- any other columns in the uploaded CSV
    # are intentionally discarded, satisfying the "other cells must be
    # empty" requirement by construction.
    student_names = [row["Name"].strip() for row in reader if row.get("Name", "").strip()]
    if not student_names:
        raise HTTPException(400, "No student names found under the 'Name' column.")

    create_class(class_name, student_names)
    return {"class_name": class_name, "student_count": len(student_names)}


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

@app.get("/classes/{class_name}/assignments")
def get_class_assignments(class_name: str):
    if not class_exists(class_name):
        raise HTTPException(404, f"Class '{class_name}' not found.")
    return {"assignments": get_assignments(class_name)}


class AssignmentCreateRequest(BaseModel):
    name: str
    due_date: str | None = None


@app.post("/classes/{class_name}/assignments")
def post_class_assignment(class_name: str, req: AssignmentCreateRequest):
    if not class_exists(class_name):
        raise HTTPException(404, f"Class '{class_name}' not found.")
    success, message = create_assignment(class_name, req.name, req.due_date)
    if not success:
        raise HTTPException(400, message)
    return {"message": message}


# ---------------------------------------------------------------------------
# Chatbot
# ---------------------------------------------------------------------------

class HistoryTurn(BaseModel):
    role: str  # "user" or "agent"
    text: str


class InstructionRequest(BaseModel):
    message: str
    history: list[HistoryTurn] = []


@app.post("/classes/{class_name}/assignments/{assignment_name}/instruct")
def post_instruct(class_name: str, assignment_name: str, req: InstructionRequest):
    if not class_exists(class_name):
        raise HTTPException(404, f"Class '{class_name}' not found.")
    assignment_names = {a["name"] for a in get_assignments(class_name)}
    if assignment_name not in assignment_names:
        raise HTTPException(404, f"Assignment '{assignment_name}' not found in '{class_name}'.")
    history = [h.model_dump() for h in req.history]
    result = call_agent(req.message, class_name, assignment_name, history=history)
    return {"result": result}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)