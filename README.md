# google-sheet-data-fill

Auto-syncs the **colourdiam.com Google Spreadsheet** (all tabs) into this repository as JSON + CSV, and auto-fills missing marketing content in the **full stock** tab (columns **X–CT** only) using an LLM.

## What's stored

Every tab of the spreadsheet is saved under `data/` as both `.json` (array of objects) and `.csv`.
`data/manifest.json` records the last sync time and row counts.

## How it stays up to date

- **Scheduled sync**: a GitHub Action (`.github/workflows/auto-sync.yml`) runs every hour and on manual dispatch.
  1. Pulls all tabs from Google Sheets into `data/`.
  2. Runs `scripts/fill_missing.py` to generate missing cells in the `full stock` tab, columns X–CT.
  3. Writes the filled cells back into the spreadsheet (when an LLM key is configured).
  4. Commits the updated data.
- **Local sync** (optional):

  ```bash
  pip install gspread oauth2client
  python scripts/sync_sheet.py --key /path/to/service-account-key.json --output data
  ```

## The fill agent (`scripts/fill_missing.py`)

Scope is intentionally narrow:

- **Tab**: only `full stock`.
- **Columns**: only `X` (PRODUCT DESCRIPTION) through `CT` (Hashtags).
- **Never touches**: identifiers, links, prices, or any column outside X–CT.
- `Status` is excluded (operational, managed elsewhere); `Last Updated` is stamped automatically on rows that were filled.

It generates the missing content with an OpenAI-compatible LLM (one call per product, JSON output), then updates the local `data/full_stock.json/.csv`. With `--write-back` it pushes the newly filled cells back to the spreadsheet.

Progress is tracked in `data/.fill_state.json` (processed SKUs) so each hourly run picks up where the last one stopped.

```bash
# Local dry run (generates + saves locally, does not touch the sheet)
python scripts/fill_missing.py --output data

# Write the filled cells back to the spreadsheet
python scripts/fill_missing.py --output data --write-back --key /path/to/service-account-key.json
```

## One-time setup (required for the scheduled sync)

### 1. Service account key

The GitHub Action needs the Google service account key as a repository secret:

```bash
base64 -w0 -i magnetic-music-503509-d6-a53de918bf57.json
```

1. Copy the base64 output (single line, no line breaks).
2. Repo → Settings → Secrets and variables → Actions → New repository secret.
3. Name: `GOOGLE_SERVICE_ACCOUNT_KEY`, value: the base64 string.

> Raw JSON also works — the workflow auto-detects it.

### 2. (Optional) LLM key for the fill agent

If you want the fill agent to generate marketing content, add an OpenAI-compatible API key:

- `USER_LLM_API_KEY` — required (any OpenAI-compatible key).
- `USER_LLM_BASE_URL` — optional, defaults to `https://api.openai.com/v1`.
- `USER_LLM_MODEL` — optional, defaults to `gpt-4o-mini`.

Add them as repository secrets (Settings → Secrets and variables → Actions). The fill step is skipped when `USER_LLM_API_KEY` is absent.

To try it locally, copy `.env.example` and export the values:

```bash
export USER_LLM_API_KEY=your-api-key-here
export USER_LLM_BASE_URL=https://api.openai.com/v1
export USER_LLM_MODEL=gpt-4o-mini
```

## Security

- The service account key must **never** be committed (it is in `.gitignore`).
- Store it only as the `GOOGLE_SERVICE_ACCOUNT_KEY` repo secret.
- LLM keys are read from environment variables (`USER_LLM_*`) — never hard-coded.
- Rotate/revoke the key in Google Cloud if it is ever exposed.
