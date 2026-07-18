"""
Homework Tracker - main FastAPI application.

Routes:
  GET  /                                                          -> web app shell
  GET  /manifest.json                                             -> PWA manifest
  GET  /classes                                                   -> list existing class sheets
  POST /classes                                                   -> create a class sheet from an uploaded CSV
  GET  /classes/{class_name}/assignments                          -> list assignment columns
  POST /classes/{class_name}/assignments                          -> create a new assignment column
  GET  /classes/{class_name}/assignments/{assignment_name}/status -> touch UI: read student statuses
  POST /classes/{class_name}/assignments/{assignment_name}/status -> touch UI: write student statuses
  POST /classes/{class_name}/assignments/{assignment_name}/instruct -> send a chat instruction, scoped to a class

Run locally:
  uvicorn main:app --reload

Deploy to Cloud Run:
  gcloud run deploy homework-tracker --source . --region us-central1 --allow-unauthenticated
"""

import csv
import io
import mimetypes
import os
from pathlib import Path

# Some environments (notably Windows, and some minimal Linux images) have
# a system mimetypes database that maps .js to "text/plain" instead of a
# JavaScript MIME type. Browsers refuse to execute <script type="module">
# unless the Content-Type is a JS type, so without this, main.js (and
# everything it imports) can silently fail to load -- symptom: the page
# loads, but nothing happens (e.g. the class list never populates).
mimetypes.add_type("text/javascript", ".js")

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
if not ENV_PATH.exists():
    print(f"[main] WARNING: no .env file found at {ENV_PATH}")
load_dotenv(dotenv_path=ENV_PATH)

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from agent import call_agent
from sheets_client import (
    ASSIGNABLE_STATUSES,
    STATUS_COLORS,
    class_exists,
    create_assignment,
    create_class,
    get_assignments,
    list_classes,
    load_records,
    update_submission_status,
)

app = FastAPI(title="Homework Tracker")

GOOGLE_OAUTH_WEB_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_WEB_CLIENT_ID", "")
ALLOWED_EMAIL = os.environ.get("ALLOWED_EMAIL", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# Paths that must stay reachable without being logged in yet.
PUBLIC_PATHS = {"/login", "/auth/google", "/manifest.json"}
PUBLIC_PREFIXES = ("/static",)


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    """
    Restricts the whole app to a single Google account, verified via
    Google Sign-In (not a shared password) -- so only the specific Gmail
    address in ALLOWED_EMAIL can ever get past the login page. Session
    state lives in a signed cookie (SessionMiddleware), so a genuine login
    is remembered across requests without re-verifying every time.

    If ALLOWED_EMAIL/SESSION_SECRET/GOOGLE_OAUTH_WEB_CLIENT_ID aren't all
    set, auth is skipped entirely -- convenient for local dev, but make
    sure all three are set before deploying anywhere publicly reachable.
    """

    async def dispatch(self, request: Request, call_next):
        if not (ALLOWED_EMAIL and SESSION_SECRET and GOOGLE_OAUTH_WEB_CLIENT_ID):
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        if request.session.get("email") == ALLOWED_EMAIL:
            return await call_next(request)

        # Browsers navigating directly get sent to the login page; API/fetch
        # calls (which the frontend JS makes) get a clean 401 instead of an
        # HTML redirect they can't do anything useful with.
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/login")
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)


app.add_middleware(LoginRequiredMiddleware)
if SESSION_SECRET:
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

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
# Auth (Google Sign-In, restricted to ALLOWED_EMAIL)
# ---------------------------------------------------------------------------

@app.get("/login")
def login_page():
    html = Path("static/login.html").read_text(encoding="utf-8")
    html = html.replace("__GOOGLE_OAUTH_WEB_CLIENT_ID__", GOOGLE_OAUTH_WEB_CLIENT_ID)
    return HTMLResponse(html)


class GoogleAuthRequest(BaseModel):
    credential: str  # the ID token from Google Sign-In


@app.post("/auth/google")
def auth_google(req: GoogleAuthRequest, request: Request):
    if not (ALLOWED_EMAIL and SESSION_SECRET and GOOGLE_OAUTH_WEB_CLIENT_ID):
        raise HTTPException(500, "Login is not configured on this server.")
    try:
        payload = google_id_token.verify_oauth2_token(
            req.credential, google_requests.Request(), GOOGLE_OAUTH_WEB_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(401, "Could not verify Google sign-in. Please try again.")

    email = payload.get("email")
    email_verified = payload.get("email_verified", False)
    if not (email_verified and email == ALLOWED_EMAIL):
        raise HTTPException(403, "This app is restricted to a specific Google account.")

    request.session["email"] = email
    return {"message": "Signed in."}


@app.post("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return {"message": "Signed out."}


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
# Touch UI: read + write submission status without the chatbot
# ---------------------------------------------------------------------------

NO_DATA_STATUS = "No Data"


@app.get("/classes/{class_name}/assignments/{assignment_name}/status")
def get_assignment_status(class_name: str, assignment_name: str):
    """
    Returns every student's current status for one assignment, for the
    touch UI. Blank cells are reported as "No Data" here ONLY -- the
    underlying sheet cell is left untouched (still blank) until the user
    actually submits a change for that student.
    """
    if not class_exists(class_name):
        raise HTTPException(404, f"Class '{class_name}' not found.")

    assignment_names = {a["name"] for a in get_assignments(class_name)}
    if assignment_name not in assignment_names:
        raise HTTPException(404, f"Assignment '{assignment_name}' not found in '{class_name}'.")

    records = load_records(class_name)
    students = []
    for record in records:
        name = record.get("Name", "").strip()
        if not name:
            continue
        raw_status = (record.get(assignment_name) or "").strip()
        students.append({
            "name": name,
            "status": raw_status if raw_status else NO_DATA_STATUS,
        })

    return {
        "assignment": assignment_name,
        "students": students,
        # Ship the color map so the frontend never hardcodes colors that
        # could drift out of sync with sheets_client.py.
        "status_colors": STATUS_COLORS,
        "assignable_statuses": sorted(ASSIGNABLE_STATUSES),
    }


class StatusUpdateItem(BaseModel):
    name: str
    status: str  # must be one of ASSIGNABLE_STATUSES ("Late" / "Not Submitted")


class StatusUpdateRequest(BaseModel):
    updates: list[StatusUpdateItem]


@app.post("/classes/{class_name}/assignments/{assignment_name}/status")
def post_assignment_status(class_name: str, assignment_name: str, req: StatusUpdateRequest):
    """
    Batched status write from the touch UI. Mirrors what the chatbot does
    via update_submission_status: any student not included here keeps
    whatever they already have (or gets auto-marked On Time only if the
    whole column was empty before this call -- same rule as before).
    """
    if not class_exists(class_name):
        raise HTTPException(404, f"Class '{class_name}' not found.")

    assignment_names = {a["name"] for a in get_assignments(class_name)}
    if assignment_name not in assignment_names:
        raise HTTPException(404, f"Assignment '{assignment_name}' not found in '{class_name}'.")

    if not req.updates:
        raise HTTPException(400, "No changes submitted.")

    updates = [
        {"row_filter": {"Name": u.name}, "status": u.status}
        for u in req.updates
    ]
    success, message = update_submission_status(class_name, assignment_name, updates)
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