"""
LLM agent: turns a free-text instruction into validated submission-status
updates for a specific assignment, using the Gemini API.

Auth: set GOOGLE_API_KEY (from Google AI Studio: https://aistudio.google.com/apikey).
"""

import os

from google import genai
from google.genai import types

from sheets_client import STATUS_LATE, STATUS_NOT_SUBMITTED, update_submission_status

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
MODEL = os.environ.get("LLM_MODEL", "gemini-3.1-flash-lite")

SYSTEM_PROMPT = f"""You are a homework-tracking assistant. You record which students were
late or did not submit a specific assignment. You may ONLY use the update_submission_status
function.

Rules:
- Only ever report students as "{STATUS_LATE}" or "{STATUS_NOT_SUBMITTED}" -- these are the
  only two statuses you may set.
- NEVER include a student who submitted on time. On-time students are handled automatically
  by the system whenever this is the first update for the assignment -- you must not mention
  them at all, even if the user's phrasing implies "everyone else is fine."
- If a single instruction affects multiple students (e.g. "Alice and Bob are late"), call
  update_submission_status ONCE with multiple entries in the `updates` list.
- If the instruction is ambiguous (e.g. unclear which students are meant, or an unclear
  status), do NOT call the function -- ask a clarifying question in plain text instead.
- Users may have typos, casual phrasing, or grammatical errors ("didnt submit", "turned in
  late", "absent", "missing") -- interpret intent charitably and map it to exactly
  "{STATUS_LATE}" or "{STATUS_NOT_SUBMITTED}".
- The conversation may include earlier turns for context. Follow-up messages like "Bob too"
  or "same for Charlie" refer to the status established in the most recent relevant turn --
  resolve them using that context rather than asking the user to repeat themselves.
- Always confirm what you recorded after a successful update."""

update_status_declaration = types.FunctionDeclaration(
    name="update_submission_status",
    description=(
        "Record submission status (Late or Not Submitted) for one or more "
        "students on the current assignment. Never include on-time students."
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "updates": types.Schema(
                type="ARRAY",
                description="List of student status updates to apply.",
                items=types.Schema(
                    type="OBJECT",
                    properties={
                        "row_filter": types.Schema(
                            type="OBJECT",
                            description="Column-value pairs identifying the student, e.g. {'Name': 'Bob'}",
                        ),
                        "status": types.Schema(
                            type="STRING",
                            description=f"Either '{STATUS_LATE}' or '{STATUS_NOT_SUBMITTED}'.",
                        ),
                    },
                    required=["row_filter", "status"],
                ),
            ),
        },
        required=["updates"],
    ),
)

tools = types.Tool(function_declarations=[update_status_declaration])

# Bound how much prior conversation gets resent each call, so cost/latency
# stay capped even in a long-running session -- this is "recent memory,"
# not full history.
MAX_HISTORY_TURNS = 12  # ~6 user/agent exchanges


def call_agent(user_message: str, class_name: str, assignment_name: str, history: list[dict] | None = None) -> str:
    prompt = f"Assignment: {assignment_name}\nInstruction: {user_message}"

    contents = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "user" if turn.get("role") == "user" else "model"
        text = turn.get("text", "")
        if text:
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))

    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[tools],
        ),
    )

    candidate = response.candidates[0]
    function_call = None
    text_reply = None

    for part in candidate.content.parts:
        if part.function_call:
            function_call = part.function_call
        elif part.text:
            text_reply = part.text

    if function_call is None:
        return text_reply or "(no response)"

    args = dict(function_call.args) if function_call.args else {}

    if "updates" not in args:
        print(f"[agent] Unexpected function call args: name={function_call.name!r} args={args!r}")
        return (
            "The assistant tried to make an update but didn't specify it correctly "
            f"(got: {args!r}). Please rephrase your instruction."
        )

    raw_updates = args["updates"]
    updates = []
    for u in raw_updates:
        u = dict(u)
        if "row_filter" in u:
            u["row_filter"] = dict(u["row_filter"])
        updates.append(u)

    if not updates:
        return "No students were specified. Please try again with a clearer instruction."

    success, message = update_submission_status(class_name, assignment_name, updates)
    return message