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
gcloud run deploy homework-tracker \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_WORKSHEET_NAME=Sheet1,LLM_MODEL=gemini-3.1-flash-lite,GOOGLE_OAUTH_TOKEN_JSON=/secrets/token.json \
  --set-secrets /secrets/token.json=google-oauth-token:latest,GOOGLE_API_KEY=gemini-api-key:latest
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

gcloud run services update homework-tracker \
  --region=us-central1 \
  --update-env-vars ALLOWED_EMAIL=your.actual.email@gmail.com,GOOGLE_OAUTH_WEB_CLIENT_ID=your-client-id.apps.googleusercontent.com \
  --update-secrets SESSION_SECRET=session-secret:latest
```
Don't forget to grant the Compute service account access to this new
secret too (same as the other secrets):
```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
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