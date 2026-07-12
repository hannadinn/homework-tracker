# Homework Tracker

A chatbot-driven homework tracker: create a class from a CSV, add assignments,
and record submissions (on time / late / not submitted) via natural language
chat. Data lives in Google Sheets (one sheet per class), color-coded by
status. Deployable to Cloud Run, with a mobile-friendly web UI.

## Project structure
```
homework-tracker/
├── main.py                    # FastAPI app: routes + serves the frontend
├── agent.py                   # LLM logic (Gemini) for submission-status tracking
├── sheets_client.py            # Google Sheets/Drive read/write via gspread
├── get_token.py                 # One-time script to authorize your Google account
├── static/
│   ├── index.html                # Frontend (classes -> assignments -> chat)
│   ├── login.html                  # Google Sign-In page
│   └── manifest.json                 # PWA manifest ("Add to Home Screen")
├── requirements.txt
├── Dockerfile
├── .dockerignore
└── .env.example
```

## How data is organized

- Each **class** is its own Google Sheet, created inside a Drive folder
  called "Homework Tracker" (auto-created on first use).
- **Row 1**: `Name` header + assignment names (added over time).
- **Row 2**: `Due Date` label + each assignment's due date.
- **Row 3+**: one row per student. Each student x assignment cell holds a
  status (`On Time` / `Late` / `Not Submitted`), color-coded green / orange
  / dark red.
- The chatbot only ever needs to be told which students were **late** or
  **did not submit** -- everyone else is automatically marked On Time the
  first time you record status for an assignment.

---

## One-time setup

### 1. Google Cloud project
1. Go to console.cloud.google.com, create (or select) a project.
2. APIs & Services -> Library -> enable:
   - **Google Sheets API**
   - **Google Drive API**

### 2. Authenticate as your own Google account (required)

Google Sheets/Drive files created via a service account count against the
service account's storage quota, which is **zero** on a personal
(non-Workspace) Google account -- so service accounts generally can't create
new files there. The fix: authenticate as *yourself* instead.

1. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID.
   - Application type: **Desktop app**. Any name is fine.
   - Download the JSON, save it as `oauth_client_secret.json` in the project folder.
2. If prompted, configure the **OAuth consent screen** (External is fine for
   personal use -- no need to publish; just add your own Google account as a
   **test user** if asked).
3. Run the one-time authorization script:
   ```bash
   pip install -r requirements.txt
   python get_token.py
   ```
   A browser window opens -- log in with your Google account and approve.
   This creates `token.json` in the project folder. **Keep this file
   private** -- it functions like a password (it's a refresh token for your
   Google account).

### 3. Gemini API key
Get a free key from aistudio.google.com/apikey (no card required for personal-scale usage).

### 4. `.env`
Copy the template and fill in your values:
```bash
cp .env.example .env
```
A minimal working `.env` (with OAuth as your auth method):
```
GOOGLE_API_KEY=your-real-gemini-key
LLM_MODEL=gemini-3.1-flash-lite

GOOGLE_OAUTH_TOKEN_JSON=./token.json

GOOGLE_WORKSHEET_NAME=Sheet1
```
Leave `GOOGLE_DRIVE_FOLDER_ID` unset -- the app auto-creates a "Homework
Tracker" folder in your Drive the first time it's needed.

---

## Environment variables reference

All variables the app reads, in one place. "Required" means the app breaks
or a feature silently disables without it -- see the Notes column.

| Variable | Required? | Where used | Notes |
|---|---|---|---|
| `GOOGLE_API_KEY` | Yes | `agent.py` | Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey). |
| `LLM_MODEL` | No (has a default) | `agent.py` | Defaults to `gemini-3.1-flash-lite` if unset. |
| `GOOGLE_OAUTH_TOKEN_JSON` | Yes (recommended) | `sheets_client.py` | Path to the file `get_token.py` generates. Required for creating new sheets -- see "Authenticate as your own Google account" above. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | No | `sheets_client.py` | Fallback only, used if `GOOGLE_OAUTH_TOKEN_JSON` is not set. Can read/write existing shared sheets but generally can't create new ones (storage quota limitation). |
| `GOOGLE_WORKSHEET_NAME` | No (has a default) | `sheets_client.py` | Tab name inside every class sheet. Defaults to `Sheet1`. |
| `GOOGLE_SHARE_EMAIL` | No | `sheets_client.py` | Only relevant with the service-account fallback -- auto-shares created sheets back to this email. Not needed with OAuth, since you already own the files. |
| `GOOGLE_DRIVE_FOLDER_ID` | No | `sheets_client.py` | Points at a specific existing Drive folder. If unset, the app auto-creates/reuses a folder named "Homework Tracker". |
| `ALLOWED_EMAIL` | No* | `main.py` | The one Google account allowed to sign in. See "Restrict access" below. |
| `SESSION_SECRET` | No* | `main.py` | Signs the login session cookie. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `GOOGLE_OAUTH_WEB_CLIENT_ID` | No* | `main.py`, `static/login.html` | OAuth **Web application** client ID (different from the Desktop one used by `get_token.py`). Safe to expose publicly. |

\* `ALLOWED_EMAIL`, `SESSION_SECRET`, and `GOOGLE_OAUTH_WEB_CLIENT_ID` are
individually optional, but **all three must be set together** for the
login gate to activate. If any one is missing, login is skipped entirely
-- convenient for local dev, but make sure all three are set before
deploying anywhere publicly reachable.

### Where each variable is set
- **Local dev**: `.env` file in the project root (loaded automatically via `python-dotenv`).
- **Cloud Run**: non-sensitive values (`LLM_MODEL`, `GOOGLE_WORKSHEET_NAME`, `ALLOWED_EMAIL`, `GOOGLE_OAUTH_WEB_CLIENT_ID`, `GOOGLE_DRIVE_FOLDER_ID`) as plain environment variables; sensitive values (`GOOGLE_API_KEY`, `GOOGLE_OAUTH_TOKEN_JSON`'s file contents, `SESSION_SECRET`) as Secret Manager secrets -- see the Deploy and Permissions sections below for exact commands.

---

## Run locally

```bash
python -m venv venv
# Windows: venv\Scripts\Activate.ps1
# Mac/Linux: source venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload
```
Visit **http://localhost:8000**. `.env` loads automatically (via
`python-dotenv`) -- no manual export step needed.

### Local Docker (optional, to test the container before deploying)
```bash
docker build -t homework-tracker .
docker run -p 8080:8080 --env-file .env homework-tracker
```
Visit **http://localhost:8080**. `.dockerignore` keeps `.env`,
`token.json`, and `service_account.json` out of the image -- they're
injected at container-start time instead.

---

## Deploy to Cloud Run

Two ways to do this: entirely through the browser (no command line at
all), or via the `gcloud` CLI. Pick whichever you're more comfortable with
-- both end up at the same result.

### Option A: Browser only (Google Cloud Console + GitHub)

**1. Get your code onto GitHub** (needed so Cloud Run has somewhere to
pull the code from -- this replaces `gcloud run deploy --source .`):
- Go to github.com, sign in (or create a free account), click **New
  repository**. Name it `homework-tracker`, set it to **Private**, create it.
- On the repo's page, click **Add file -> Upload files**, then drag in
  every file/folder from your project *except* `.env`, `token.json`,
  `service_account.json`, `oauth_client_secret.json`, and `venv/` -- these
  contain secrets or are local-only and should never be uploaded.
  Commit the upload.

**2. Store your secrets in Secret Manager:**
- In the Cloud Console, go to **Secret Manager** (search it in the top
  search bar) -> **Create Secret**.
- Name: `gemini-api-key`. Secret value: paste your Gemini API key. Create.
- Repeat: name `google-oauth-token`, and for the value, click **Upload
  file** and select your local `token.json`. Create.

**3. Create the Cloud Run service:**
- Go to **Cloud Run** -> **Create Service**.
- Choose **Continuously deploy from a repository (source or function)** ->
  **Set up with Cloud Build** -> connect your GitHub account -> select
  your `homework-tracker` repo and the `main` branch.
- Build type: it should auto-detect the `Dockerfile` -- confirm that's
  selected.
- Service name: `homework-tracker`. Region: pick one near you (e.g.
  `us-central1`).
- Authentication: **Allow unauthenticated invocations** (so the URL works
  without a Google login -- see the caution note below).

**4. Set environment variables and secrets** (still on the same creation
page, expand **Container, Networking, Security** -> **Variables & Secrets** tab):
- Under **Environment variables**, add:
  - `GOOGLE_WORKSHEET_NAME` = `Sheet1`
  - `LLM_MODEL` = `gemini-3.1-flash-lite`
  - `GOOGLE_OAUTH_TOKEN_JSON` = `/secrets/token.json`
- Under **Secrets**, click **Reference a secret** twice:
  - Mount `google-oauth-token` as a **volume**, mount path `/secrets`,
    file name `token.json` (so it ends up at `/secrets/token.json`,
    matching the env var above).
  - Expose `gemini-api-key` as an **environment variable** named
    `GOOGLE_API_KEY`.

**5. Click Create.** Cloud Build will build and deploy automatically --
watch progress in the **Cloud Build** page if you want. Once done, the
service's URL (shown at the top of its Cloud Run page) is your app --
something like `https://homework-tracker-xxxxx-uc.a.run.app`.

From now on, any time you push new commits to that GitHub repo (e.g.
re-uploading changed files the same way), Cloud Run automatically rebuilds
and redeploys -- no need to repeat these steps.

### Option B: `gcloud` CLI

**0. Install the CLI** (skip if you already have it -- check with `gcloud --version`):
- Go to `cloud.google.com/sdk/docs/install`, download the installer for your OS
  (Windows: `GoogleCloudSDKInstaller.exe`), and run it. It installs `gcloud`
  and offers to add itself to your PATH automatically.
- Open a **new** terminal window after installing (PATH changes only apply
  to new sessions), then verify:
  ```bash
  gcloud --version
  ```

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```
`gcloud auth login` opens a browser window to log in with the same Google
account your GCP project is under. `YOUR_PROJECT_ID` can be found in the
Cloud Console top bar, or under **IAM & Admin -> Settings**.

Sanity check:
```bash
gcloud projects list
```
If that lists your project(s) without error, you're ready for the steps below.

**1. Store secrets:**
```bash
gcloud secrets create gemini-api-key --data-file=- <<< "your-gemini-key"
gcloud secrets create google-oauth-token --data-file=token.json
```

**2. Deploy:**
```bash
gcloud run deploy homework-tracker --source . --region us-central1 --allow-unauthenticated --set-env-vars GOOGLE_WORKSHEET_NAME=Sheet1,LLM_MODEL=gemini-3.1-flash-lite,GOOGLE_OAUTH_TOKEN_JSON=/secrets/token.json --set-secrets /secrets/token.json=google-oauth-token:latest,GOOGLE_API_KEY=gemini-api-key:latest
```
This mounts the OAuth token as a file at `/secrets/token.json` inside the
container (rather than as a raw env var, since it's JSON) and points
`GOOGLE_OAUTH_TOKEN_JSON` at that path.

Cloud Run prints a URL like `https://homework-tracker-xxxxx-uc.a.run.app` -- that's your app.

> Either option: `--allow-unauthenticated` / "Allow unauthenticated
> invocations" makes the URL publicly *reachable* without a Google login at
> the infrastructure level. That's fine -- the app itself gates real access
> behind Google Sign-In (see below), so this setting only controls whether
> the connection is accepted, not whether anyone can actually use the app.

### Token refresh in production
OAuth access tokens expire (usually hourly), but `token.json` includes a
**refresh token**, and `sheets_client.py`'s `_get_credentials()` already
auto-refreshes it in memory on each cold start. Refresh tokens themselves
can eventually be revoked or expire from long inactivity (commonly ~6
months for consent-screen apps still in "Testing" mode) -- if that happens,
rerun `get_token.py` locally, then update the secret:
- **Browser**: Secret Manager -> `google-oauth-token` -> **New Version** -> upload the new `token.json`.
- **CLI**: `gcloud secrets versions add google-oauth-token --data-file=token.json`

---

## IAM permissions required

Deploying with `--source .` (Option B) involves **two separate identities**,
and both commonly need permissions granted manually on a fresh project --
this section pulls together every grant referenced earlier in one place.
Commands below are **PowerShell** (Windows).

### 1. Your own account (running `gcloud`)
If you created the GCP project yourself, you're already the Owner and
don't need to do anything here. This only matters if someone else created
the project and added you afterward -- in that case you'd need at least
the **Editor** role to run the commands in this README.

### 2. The Compute Engine default service account
Cloud Run uses this service account both to **build** your container (via
Cloud Build, when using `--source .`) and to **run** it -- meaning it
needs access to read your uploaded source and to read your secrets at
runtime.

**Find your project number** (needed to build the service account's email):
```powershell
gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)"
```
This prints a number, e.g. `631307158275`. The service account's full
email is always `<that number>-compute@developer.gserviceaccount.com`.

**Grant the roles it needs:**
```powershell
$PROJECT_ID = "YOUR_PROJECT_ID"
$PROJECT_NUMBER = "631307158275"  # from the command above
$SA = "$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/cloudbuild.builds.builder"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/storage.objectViewer"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
```

| Role | Why it's needed |
|---|---|
| `roles/cloudbuild.builds.builder` | Lets Cloud Build actually build your container image from source. |
| `roles/storage.objectViewer` | Lets it read the zipped source `gcloud` uploads to a temporary bucket. |
| `roles/secretmanager.secretAccessor` | Lets the running container read `GOOGLE_API_KEY`, `GOOGLE_OAUTH_TOKEN_JSON`, and `SESSION_SECRET` (or whichever of these you've stored as secrets) at startup. |

**Via the browser instead:** Cloud Console -> **IAM & Admin -> IAM** ->
find the row ending in `-compute@developer.gserviceaccount.com` -> pencil
icon -> **Add Another Role** -> add each of the three roles above -> Save.

### 3. Your Google account's own Sheets/Drive access (OAuth)
This is *not* an IAM grant in the usual sense -- it's the one-time consent
you give yourself when running `get_token.py` (see "Authenticate as your
own Google account" earlier). No service account or IAM policy is
involved; your account already owns whatever it creates.

### Common permission errors and their fix
| Error | Fix |
|---|---|
| `Build failed because the default service account is missing required IAM permissions` | Grant `roles/cloudbuild.builds.builder` and `roles/storage.objectViewer` (section 2 above). |
| `Permission denied on secret ... The service account used must be granted the 'Secret Manager Secret Accessor' role` | Grant `roles/secretmanager.secretAccessor` (section 2 above). |
| `Error: Forbidden` / `Your client does not have permission to get URL / from this server` when visiting the app URL | The Cloud Run service itself isn't allowing public requests. Fix: `gcloud run services add-iam-policy-binding homework-tracker --region=us-central1 --member="allUsers" --role="roles/run.invoker"` (this controls whether the URL is *reachable* -- actual access is still gated by Google Sign-In once you've set that up). |
| `gspread.exceptions.APIError: [403] storage quota exceeded` | Not an IAM issue -- you're using the service account instead of OAuth for creating sheets. See "Authenticate as your own Google account" earlier. |

---

## Restrict access to your own Google account

By default (`--allow-unauthenticated`), anyone with the Cloud Run URL can
*reach* the app. To make sure only **you** can actually use it, the app
has a built-in login gate using Google Sign-In -- restricted to one exact
email address, verified by Google itself (not a shared password anyone
could leak or guess).

### 1. Create a "Web application" OAuth client
This is separate from the "Desktop app" one used by `get_token.py`.
- Cloud Console -> **APIs & Services -> Credentials -> Create Credentials -> OAuth client ID**.
- Application type: **Web application**.
- Under **Authorized JavaScript origins**, add:
  - `http://localhost:8000` (for local testing)
  - Your Cloud Run URL once you have it, e.g. `https://homework-tracker-xxxxx-uc.a.run.app`
- Create, then copy the **Client ID** (a long string ending in `.apps.googleusercontent.com`).

### 2. Generate a session secret
This signs the login cookie so it can't be forged:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Local `.env`
```
ALLOWED_EMAIL=your.actual.email@gmail.com
SESSION_SECRET=<the random string from step 2>
GOOGLE_OAUTH_WEB_CLIENT_ID=<the Web client ID from step 1>
```
Restart the server -- visiting `http://localhost:8000` should now redirect
to `/login` and show a Google Sign-In button. Only the exact email in
`ALLOWED_EMAIL` will be let through.

### 4. Cloud Run
`SESSION_SECRET` is sensitive -- store it as a secret. The other two are
not sensitive on their own (an email address and a client ID meant to be
public) and can be plain environment variables.

**Browser:** Secret Manager -> Create Secret -> name `session-secret` ->
paste the value from step 2. Then on your Cloud Run service's **Variables
& Secrets** tab: add `ALLOWED_EMAIL` and `GOOGLE_OAUTH_WEB_CLIENT_ID` as
environment variables, and reference `session-secret` as a secret exposed
as environment variable `SESSION_SECRET`.

**CLI:**
```bash
python -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets create session-secret --data-file=-

gcloud run services update homework-tracker --region=us-central1 --update-env-vars ALLOWED_EMAIL=your.actual.email@gmail.com,GOOGLE_OAUTH_WEB_CLIENT_ID=your-client-id.apps.googleusercontent.com --update-secrets SESSION_SECRET=session-secret:latest
```
Don't forget to grant the Compute service account access to this new
secret too (same as the other secrets):
```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
```

If all three env vars aren't set, the login gate is skipped entirely --
convenient for local dev without bothering with OAuth setup, but make sure
all three are configured before/when you deploy publicly.

---

## View it on your iPhone

1. Open **Safari** (required for "Add to Home Screen" to pick up the manifest).
2. Navigate to your Cloud Run URL.
3. Tap the **Share** icon -> **Add to Home Screen** -> **Add**.
4. You now have a home-screen app icon that opens full-screen, no browser bar.

---

## Cost expectations

- **Cloud Run**: effectively $0/month at personal-scale usage (well within
  the Always Free tier).
- **Gemini API**: `gemini-3.1-flash-lite` has a generous free daily quota;
  personal-scale usage should stay within it.
- **Google Sheets/Drive**: free, using your normal account storage (each
  class sheet and the "Homework Tracker" folder are tiny).

## Troubleshooting

- **`gspread.exceptions.APIError: [403] storage quota exceeded`** -- you're
  using the service account instead of OAuth, or `GOOGLE_OAUTH_TOKEN_JSON`
  isn't set/found. Re-check step 2 above.
- **`KeyError` for an env var at startup** -- check `.env` exists in the
  same folder as `main.py`, and that `main.py`'s startup log doesn't print
  a "no .env file found" warning.
- **`WorksheetNotFound`** -- `GOOGLE_WORKSHEET_NAME` doesn't match the tab
  name inside the sheet (check the tab at the bottom of the sheet, not the
  spreadsheet's title).
- **Assignment cells not colored** -- color-coding only applies via the
  chatbot's `update_submission_status` action; manually typed values in
  Sheets won't get colors automatically.
- **Google Sign-In button doesn't appear / errors in browser console** --
  double check `GOOGLE_OAUTH_WEB_CLIENT_ID` is the **Web application**
  client ID (not the Desktop one from `get_token.py`), and that the exact
  URL you're visiting is listed under that client's Authorized JavaScript
  origins in Cloud Console.
- **"This app is restricted to a specific Google account" after signing
  in** -- `ALLOWED_EMAIL` doesn't match the Google account you signed in
  with, exactly (case-sensitive, no typos).

## Possible next steps
- Add a `GET /classes/{class_name}/data` endpoint + frontend table view to
  see the full grade sheet without switching to Google Sheets.
- Prompt-optimization loop for the agent's system prompt if you start
  hitting recurring misinterpretations.