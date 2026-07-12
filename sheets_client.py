"""
Google Sheets access layer.

Each "class" is its own Google Sheet (spreadsheet), created dynamically via
the app. Every class sheet follows a fixed format:
  - Column A, row 1: "Name"
  - Column A, rows 2+: student names
  - Column B onwards, row 1: assignment names (added over time)

Auth: uses a service account JSON key. In production (Cloud Run), store the
key contents in an environment variable or Secret Manager rather than
shipping the JSON file in your container image.

Setup steps (one-time):
  1. Create a GCP service account, download its JSON key.
  2. Enable the Google Sheets API AND Google Drive API for your project.
  3. Set env vars: GOOGLE_SERVICE_ACCOUNT_JSON (raw JSON contents, or a path
     to the file locally), and optionally GOOGLE_SHARE_EMAIL (see below).

Note on visibility: sheets created by the service account are owned by the
service account, not by you. Set GOOGLE_SHARE_EMAIL to your own Google
account email and newly created class sheets will automatically be shared
with you as an editor, so you can also view/edit them directly in Google
Sheets, not just through this app.
"""

import difflib
import json
import os
from datetime import date, datetime
from functools import lru_cache

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

WORKSHEET_NAME = os.environ.get("GOOGLE_WORKSHEET_NAME", "Sheet1")
SHARE_EMAIL = os.environ.get("GOOGLE_SHARE_EMAIL")  # optional


def _using_oauth() -> bool:
    """True if authenticating as the real user (OAuth) rather than the
    service account. When true, files are already owned by the user, so
    the GOOGLE_SHARE_EMAIL self-sharing step is unnecessary."""
    return bool(os.environ.get("GOOGLE_OAUTH_TOKEN_JSON"))

# Optional: restrict all class sheets to one Drive folder. If unset, the
# app auto-creates (or reuses) a folder named "Homework Tracker" the first
# time it's needed. Set this explicitly only if you want to point at a
# specific existing folder instead.
DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")  # optional

# Row layout within every class sheet:
#   Row 1: "Name" | Assignment 1 | Assignment 2 | ...   <- headers
#   Row 2: "Due Date" | 2 Jun 2026 | 5 Jun 2026 ...      <- assignment metadata
#   Row 3+: student names | grades ...                   <- actual student data
HEADER_ROW = 1
DATE_ROW = 2
FIRST_DATA_ROW = 3

DUE_DATE_STRPTIME_FORMAT = "%d %b %Y"  # e.g. "2 Jun 2026" (Python's %d also accepts no leading zero)

# Submission status values written into student x assignment cells, each
# with a background color (Google Sheets API color = RGB floats 0-1).
STATUS_ON_TIME = "On Time"
STATUS_LATE = "Late"
STATUS_NOT_SUBMITTED = "Not Submitted"

STATUS_COLORS = {
    STATUS_ON_TIME: {"red": 0.0, "green": 0.39, "blue": 0.0},        # dark green
    STATUS_LATE: {"red": 1.0, "green": 0.65, "blue": 0.0},           # orange
    STATUS_NOT_SUBMITTED: {"red": 0.55, "green": 0.0, "blue": 0.0},  # dark red
}

# Statuses a user/LLM is allowed to explicitly set. "On Time" is derived
# automatically by the auto-fill rule and is never set directly.
ASSIGNABLE_STATUSES = {STATUS_LATE, STATUS_NOT_SUBMITTED}


def _format_due_date(d: date) -> str:
    # %-d (no leading zero) isn't portable across POSIX/Windows strftime,
    # so build the "2 Jun 2026" format manually instead of relying on it.
    return f"{d.day} {d.strftime('%b %Y')}"


def _parse_due_date_lenient(s: str):
    """Parse a 'Due Date' cell value like '2 Jun 2026' for sorting purposes.
    Returns datetime.min on empty/unparseable values so they sort last."""
    s = (s or "").strip()
    if not s:
        return datetime.min
    try:
        return datetime.strptime(s, DUE_DATE_STRPTIME_FORMAT)
    except ValueError:
        return datetime.min


def _parse_due_date_strict(s: str) -> date:
    """Parse user-supplied due date input. Raises ValueError if invalid --
    used to validate input at assignment-creation time."""
    return datetime.strptime(s.strip(), DUE_DATE_STRPTIME_FORMAT).date()

# In-memory cache: class_name -> spreadsheet ID. Avoids re-searching Drive
# by title on every single request, which is the slow part of open(name).
# This is fine for a single-process dev server; for multi-instance Cloud Run
# deployments each instance keeps its own cache, which is still a win since
# it just means a cold cache per instance rather than per request.
_sheet_id_cache: dict[str, str] = {}


@lru_cache(maxsize=1)
def _get_credentials():
    """
    Prefer OAuth credentials for your own Google account (GOOGLE_OAUTH_TOKEN_JSON)
    -- required for creating new files, since service accounts have zero
    Drive storage quota on personal (non-Workspace) accounts and generally
    cannot create files. See get_token.py for one-time setup.

    Falls back to the service account (GOOGLE_SERVICE_ACCOUNT_JSON) if no
    OAuth token is configured -- still useful for read/write access to
    sheets that already exist and are shared with it, just not for
    creating new ones.
    """
    oauth_raw = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
    if oauth_raw:
        if os.path.isfile(oauth_raw):
            creds = OAuthCredentials.from_authorized_user_file(oauth_raw, scopes=SCOPES)
        else:
            info = json.loads(oauth_raw)
            creds = OAuthCredentials.from_authorized_user_info(info, scopes=SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds

    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    if os.path.isfile(raw):
        return ServiceAccountCredentials.from_service_account_file(raw, scopes=SCOPES)
    info = json.loads(raw)
    return ServiceAccountCredentials.from_service_account_info(info, scopes=SCOPES)


@lru_cache(maxsize=1)
def _get_client():
    return gspread.authorize(_get_credentials())


@lru_cache(maxsize=1)
def _get_drive_service():
    # Imported lazily so this dependency is only needed if folder
    # auto-provisioning is actually used.
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)


DEFAULT_FOLDER_NAME = "Homework Tracker"


@lru_cache(maxsize=1)
def _get_or_create_default_folder_id() -> str:
    """
    Find a Drive folder named 'Homework Tracker' that this service account
    can see; create it if it doesn't exist yet. Cached for the life of the
    process, since the folder (once created) never needs to be looked up
    again. Only used when GOOGLE_DRIVE_FOLDER_ID isn't explicitly set.
    """
    service = _get_drive_service()
    query = (
        f"name='{DEFAULT_FOLDER_NAME}' and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    folder = service.files().create(
        body={"name": DEFAULT_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    folder_id = folder["id"]

    if SHARE_EMAIL and not _using_oauth():
        # So the folder (and everything created inside it) is visible in
        # the user's own Drive, not just accessible via the app. Only
        # needed when the service account owns the file -- with OAuth the
        # user already owns it directly.
        service.permissions().create(
            fileId=folder_id,
            body={"type": "user", "role": "writer", "emailAddress": SHARE_EMAIL},
            fields="id",
        ).execute()

    return folder_id


def _get_drive_folder_id() -> str:
    """Explicit GOOGLE_DRIVE_FOLDER_ID always wins; otherwise auto-provision
    (or reuse) a 'Homework Tracker' folder."""
    return DRIVE_FOLDER_ID or _get_or_create_default_folder_id()


def _resolve_sheet_id(class_name: str) -> str:
    """Look up a spreadsheet's ID by title, using the cache when possible."""
    if class_name in _sheet_id_cache:
        return _sheet_id_cache[class_name]

    client = _get_client()
    files = client.list_spreadsheet_files(title=class_name, folder_id=_get_drive_folder_id())
    if not files:
        raise gspread.exceptions.SpreadsheetNotFound(class_name)

    sheet_id = files[0]["id"]
    _sheet_id_cache[class_name] = sheet_id
    return sheet_id


def _get_worksheet(class_name: str):
    client = _get_client()
    sheet_id = _resolve_sheet_id(class_name)
    try:
        return client.open_by_key(sheet_id).worksheet(WORKSHEET_NAME)
    except gspread.exceptions.APIError:
        # Cached ID might be stale (sheet deleted/renamed) -- clear and retry once.
        _sheet_id_cache.pop(class_name, None)
        sheet_id = _resolve_sheet_id(class_name)
        return client.open_by_key(sheet_id).worksheet(WORKSHEET_NAME)


# ---------------------------------------------------------------------------
# Class list management
# ---------------------------------------------------------------------------

def list_classes() -> list[str]:
    """Return the names of all spreadsheets the service account can see,
    scoped to the app's Drive folder (explicit or auto-provisioned).
    Also warms the sheet-ID cache as a side effect."""
    client = _get_client()
    files = client.list_spreadsheet_files(folder_id=_get_drive_folder_id())
    for f in files:
        _sheet_id_cache[f["name"]] = f["id"]
    return sorted(f["name"] for f in files)


def class_exists(class_name: str) -> bool:
    if class_name in _sheet_id_cache:
        return True
    client = _get_client()
    files = client.list_spreadsheet_files(title=class_name, folder_id=_get_drive_folder_id())
    if files:
        _sheet_id_cache[class_name] = files[0]["id"]
        return True
    return False


def create_class(class_name: str, student_names: list[str]) -> None:
    """
    Create a new class sheet with the layout the rest of the app expects:
      Row 1: "Name" header
      Row 2: "Due Date" label (populated per-column as assignments are added)
      Row 3+: student names

    The uploaded CSV only ever needs "Name" in A1 and student names in A2+;
    this shifts them down to A3+ automatically, so the user never has to
    think about the row layout themselves.

    Any other columns from the uploaded CSV are never written -- this
    always starts from a blank sheet, satisfying the "other columns must
    be empty" requirement by construction.
    """
    client = _get_client()
    sh = client.create(class_name, folder_id=_get_drive_folder_id())
    _sheet_id_cache[class_name] = sh.id
    ws = sh.sheet1
    if ws.title != WORKSHEET_NAME:
        ws.update_title(WORKSHEET_NAME)

    rows = [["Name"], ["Due Date"]] + [[name] for name in student_names]
    ws.update(rows, "A1")

    if SHARE_EMAIL and not _using_oauth():
        sh.share(SHARE_EMAIL, perm_type="user", role="writer")


# ---------------------------------------------------------------------------
# Assignment (column) management
# ---------------------------------------------------------------------------

def get_assignments(class_name: str) -> list[dict]:
    """
    Return assignments as [{"name": ..., "due_date": ...}, ...],
    sorted by due_date descending (latest first). Assignments with a
    missing/unparseable date sort last.
    """
    ws = _get_worksheet(class_name)
    header = ws.row_values(HEADER_ROW)
    date_row = ws.row_values(DATE_ROW)

    assignments = []
    for idx, col_name in enumerate(header[1:], start=1):  # skip "Name" column
        date_str = date_row[idx] if idx < len(date_row) else ""
        assignments.append({"name": col_name, "due_date": date_str})

    assignments.sort(key=lambda a: _parse_due_date_lenient(a["due_date"]), reverse=True)
    return assignments


def create_assignment(class_name: str, assignment_name: str, due_date: str | None = None):
    """
    Add a new assignment column + its Due Date. `due_date` must be in
    "D Mon YYYY" format (e.g. "2 Jun 2026") if provided; if empty/None,
    defaults to today. Returns (success: bool, message: str).
    """
    assignment_name = assignment_name.strip()
    if not assignment_name:
        return False, "Assignment name cannot be empty."

    due_date = (due_date or "").strip()
    if due_date:
        try:
            _parse_due_date_strict(due_date)
        except ValueError:
            return False, f"Invalid due date '{due_date}'. Expected format like '2 Jun 2026'."
        date_str = due_date
    else:
        date_str = _format_due_date(date.today())

    ws = _get_worksheet(class_name)
    header = ws.row_values(HEADER_ROW)

    if any(h.strip().lower() == assignment_name.lower() for h in header):
        return False, f"An assignment named '{assignment_name}' already exists."

    col_index = len(header) + 1

    ws.update_cell(HEADER_ROW, col_index, assignment_name)
    ws.update_cell(DATE_ROW, col_index, date_str)

    return True, f"Created assignment '{assignment_name}' (due {date_str})."


# ---------------------------------------------------------------------------
# Data read/write (used by the chatbot agent)
# ---------------------------------------------------------------------------

def _get_student_records(ws) -> list[dict]:
    """Build student records manually, skipping the Date Created row (row 2).
    Equivalent to ws.get_all_records() but immune to the extra metadata row."""
    values = ws.get_all_values()
    if len(values) < HEADER_ROW:
        return []
    header = values[HEADER_ROW - 1]
    data_rows = values[FIRST_DATA_ROW - 1:]  # 0-indexed: row 3 -> index 2
    records = []
    for row in data_rows:
        row = row + [""] * (len(header) - len(row))  # pad short rows
        records.append(dict(zip(header, row)))
    return records


def load_records(class_name: str) -> list[dict]:
    ws = _get_worksheet(class_name)
    return _get_student_records(ws)


def get_columns(class_name: str) -> list[str]:
    ws = _get_worksheet(class_name)
    return ws.row_values(1)


def _normalize(v) -> str:
    return str(v).strip().lower()


def _find_row_matches(records: list[dict], row_filter: dict):
    """
    Find records matching row_filter (column: value pairs). Exact match
    (case/whitespace-insensitive) first; fuzzy fallback for typos.
    Returns (matches: list[int], notes: list[str]).
    """
    exact = [
        i for i, record in enumerate(records)
        if all(_normalize(record.get(k)) == _normalize(v) for k, v in row_filter.items())
    ]
    if exact:
        return exact, []

    notes = []
    candidate_sets = []
    for k, v in row_filter.items():
        column_values = [_normalize(record.get(k)) for record in records]
        close = difflib.get_close_matches(_normalize(v), column_values, n=3, cutoff=0.6)
        if not close:
            return [], [f"No value close to '{v}' found in column '{k}'."]
        best = close[0]
        matched_indices = [i for i, cv in enumerate(column_values) if cv == best]
        candidate_sets.append(set(matched_indices))
        if best != _normalize(v):
            original_value = records[matched_indices[0]].get(k)
            notes.append(f"interpreted '{v}' as '{original_value}'")

    matches = set.intersection(*candidate_sets) if candidate_sets else set()
    return list(matches), notes


def update_cells_batch(class_name: str, updates: list[dict]):
    """
    Apply multiple cell updates in one go. Each item in `updates` is a dict
    with keys: row_filter, column, new_value.

    Validates every update BEFORE applying any of them, so a batch either
    fully succeeds or fails without partially updating the sheet. Row
    matching is case/whitespace-insensitive, with a fuzzy-match fallback for
    typos (surfaced back to the caller so mismatches are never silent).
    """
    ws = _get_worksheet(class_name)
    header = ws.row_values(HEADER_ROW)
    records = _get_student_records(ws)

    resolved = []
    errors = []

    for u in updates:
        row_filter = u["row_filter"]
        column = u["column"]
        new_value = u["new_value"]

        if column not in header:
            errors.append(f"Column '{column}' does not exist.")
            continue

        bad_filter_col = next((c for c in row_filter if c not in header), None)
        if bad_filter_col:
            errors.append(f"Filter column '{bad_filter_col}' does not exist.")
            continue

        matches, notes = _find_row_matches(records, row_filter)

        if len(matches) == 0:
            if notes:
                errors.append(f"For filter {row_filter}: {'; '.join(notes)}")
            else:
                errors.append(f"No row found matching {row_filter} (even with fuzzy matching).")
            continue
        if len(matches) > 1:
            errors.append(f"Filter {row_filter} matches {len(matches)} rows — please be more specific.")
            continue

        row_number = matches[0] + FIRST_DATA_ROW
        col_number = header.index(column) + 1
        old_value = records[matches[0]].get(column)
        resolved.append((row_number, col_number, old_value, new_value, row_filter, column, notes))

    if errors:
        return False, "No changes made — please fix the following: " + "; ".join(errors)

    cell_list = []
    summaries = []
    for row_number, col_number, old_value, new_value, row_filter, column, notes in resolved:
        cell = ws.cell(row_number, col_number)
        cell.value = new_value
        cell_list.append(cell)
        note_str = f" ({'; '.join(notes)})" if notes else ""
        summaries.append(f"{row_filter} '{column}': '{old_value}' → '{new_value}'{note_str}")

    ws.update_cells(cell_list)
    return True, "Updated " + "; ".join(summaries)


# ---------------------------------------------------------------------------
# Submission status (color-coded grading)
# ---------------------------------------------------------------------------

def _status_cell_request(sheet_id: int, row_number: int, col_number: int, status: str) -> dict:
    """Build a Sheets API repeatCell request that sets both the cell's text
    value and its background color in a single operation."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_number - 1,
                "endRowIndex": row_number,
                "startColumnIndex": col_number - 1,
                "endColumnIndex": col_number,
            },
            "cell": {
                "userEnteredValue": {"stringValue": status},
                "userEnteredFormat": {"backgroundColor": STATUS_COLORS[status]},
            },
            "fields": "userEnteredValue,userEnteredFormat.backgroundColor",
        }
    }


def update_submission_status(class_name: str, assignment_name: str, updates: list[dict]):
    """
    Record submission status for specific students on a specific assignment.

    `updates` is a list of {"row_filter": {...}, "status": "Late"|"Not Submitted"}.
    Only these two statuses may be set explicitly -- "On Time" is never set
    directly by a caller.

    Auto-fill rule: if the assignment column has NO values at all before
    this call, every student NOT mentioned in `updates` is automatically
    set to "On Time". If the column already has any values, only the
    students explicitly mentioned are touched.

    All cell writes (text + background color) happen in a single batched
    Sheets API call. Returns (success: bool, message: str).
    """
    ws = _get_worksheet(class_name)
    header = ws.row_values(HEADER_ROW)

    if assignment_name not in header:
        return False, f"There's no assignment called '{assignment_name}' here."
    col_number = header.index(assignment_name) + 1

    records = _get_student_records(ws)

    # Was the column empty *before* this update? Checked against the raw
    # sheet values (not the parsed records) to avoid ambiguity from padding.
    existing_col_values = ws.col_values(col_number)
    existing_data_values = existing_col_values[FIRST_DATA_ROW - 1:]
    was_empty = not any(v.strip() for v in existing_data_values)

    resolved = []  # (row_number, status, display_name, notes)
    errors = []
    touched_rows = set()

    def display_name(row_filter, matched_index=None):
        """Prefer the student's actual on-record name over the raw filter dict."""
        if matched_index is not None:
            name = records[matched_index].get("Name")
            if name:
                return name
        # Fall back to whatever value was used to identify them.
        return next(iter(row_filter.values()), "that student")

    for u in updates:
        row_filter = u["row_filter"]
        status = u["status"].strip() if isinstance(u.get("status"), str) else ""
        who = display_name(row_filter)

        if status not in ASSIGNABLE_STATUSES:
            errors.append(
                f"'{status}' isn't a status I can set for {who} -- "
                f"it needs to be either '{STATUS_LATE}' or '{STATUS_NOT_SUBMITTED}'."
            )
            continue

        matches, notes = _find_row_matches(records, row_filter)

        if len(matches) == 0:
            errors.append(f"I couldn't find a student matching '{who}' on the class list.")
            continue
        if len(matches) > 1:
            errors.append(f"More than one student matches '{who}' -- could you be more specific?")
            continue

        row_number = matches[0] + FIRST_DATA_ROW
        who = display_name(row_filter, matched_index=matches[0])
        resolved.append((row_number, status, who, notes))
        touched_rows.add(row_number)

    if errors:
        return False, "I couldn't make any changes: " + " ".join(errors)

    requests = []
    by_status: dict[str, list[str]] = {}
    typo_notes = []
    for row_number, status, who, notes in resolved:
        requests.append(_status_cell_request(ws.id, row_number, col_number, status))
        by_status.setdefault(status, []).append(who)
        if notes:
            typo_notes.append(f"assumed you meant '{who}'")

    def join_natural(names: list[str]) -> str:
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return ", ".join(names[:-1]) + f", and {names[-1]}"

    sentences = []
    for status, names in by_status.items():
        verb = "was" if len(names) == 1 else "were"
        sentences.append(f"{join_natural(names)} {verb} marked {status}.")

    if was_empty:
        remaining = len(records) - len(touched_rows)
        if remaining > 0:
            noun = "student" if remaining == 1 else "students"
            verb = "was" if remaining == 1 else "were"
            sentences.append(f"The remaining {remaining} {noun} {verb} marked On Time.")
        for i, _record in enumerate(records):
            row_number = i + FIRST_DATA_ROW
            if row_number not in touched_rows:
                requests.append(_status_cell_request(ws.id, row_number, col_number, STATUS_ON_TIME))

    if typo_notes:
        sentences.append("(" + "; ".join(typo_notes) + ")")

    if not requests:
        return False, "No changes to apply."

    ws.spreadsheet.batch_update({"requests": requests})
    return True, " ".join(sentences)