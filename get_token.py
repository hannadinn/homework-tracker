"""
Run this ONCE, locally, to authorize the app to create/edit Google Sheets
under YOUR OWN Google account (not the service account).

Why: service accounts have zero Drive storage quota and generally cannot
create new files in a personal (non-Workspace) Google Drive. Authenticating
as yourself avoids that limitation entirely -- files are created under your
normal account quota, same as if you created them by hand.

One-time setup before running this script:
  1. In Google Cloud Console (same project as your service account):
     APIs & Services -> Credentials -> Create Credentials -> OAuth client ID.
  2. Application type: "Desktop app". Give it any name.
  3. Download the JSON -- save it as oauth_client_secret.json in this folder.
  4. Make sure the OAuth consent screen is configured (External is fine for
     personal use; you don't need to publish it, just add your own Google
     account as a "test user" if prompted).

Then run:
    python get_token.py

A browser window will open asking you to log in and grant access. After
you approve, a token.json file will be created in this folder -- that's
what the app uses going forward. Keep it secret (same sensitivity as a
password); don't commit it to version control.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CLIENT_SECRET_FILE = "oauth_client_secret.json"
TOKEN_FILE = "token.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\nSuccess! Saved credentials to {TOKEN_FILE}.")
    print("Set GOOGLE_OAUTH_TOKEN_JSON in your .env to point at this file, e.g.:")
    print(f"  GOOGLE_OAUTH_TOKEN_JSON=./{TOKEN_FILE}")


if __name__ == "__main__":
    main()