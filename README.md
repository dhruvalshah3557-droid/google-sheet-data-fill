# google-sheet-data-fill

Auto-syncs the **colourdiam.com Google Spreadsheet** (all tabs) into this repository as JSON + CSV.

## What's stored

Every tab of the spreadsheet is saved under `data/` as both `.json` (array of objects) and `.csv`.
`data/manifest.json` records the last sync time and row counts.

## How it stays up to date

- **Scheduled sync**: a GitHub Action (`.github/workflows/auto-sync.yml`) runs every 6 hours and on manual dispatch.
- **Local sync** (optional):

  ```bash
  pip install gspread oauth2client
  python scripts/sync_sheet.py --key /path/to/service-account-key.json --output data
  ```

## One-time setup (required for the scheduled sync)

The GitHub Action needs the Google service account key as a repository secret:

```bash
base64 -i magnetic-music-503509-d6-a53de918bf57.json
```

1. Copy the base64 output.
2. Repo → Settings → Secrets and variables → Actions → New repository secret.
3. Name: `GOOGLE_SERVICE_ACCOUNT_KEY`, value: the base64 string.

## Security

- The service account key must **never** be committed (it is in `.gitignore`).
- Store it only as the `GOOGLE_SERVICE_ACCOUNT_KEY` repo secret.
- Rotate/revoke the key in Google Cloud if it is ever exposed.
