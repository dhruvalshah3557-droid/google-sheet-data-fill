#!/usr/bin/env python3
"""Open/update/close the GitHub "Data Quality Monitor" issue from check_report.json.

Run after `python scripts/agent.py --check-only --report` inside a GitHub Action
so the audit findings are visible on the repo 24/7:

  - issues found  -> create or refresh the issue with the latest findings
  - no issues     -> close the issue (if open)

Env:
  GITHUB_TOKEN       token with issues:write (e.g. ${{ secrets.GITHUB_TOKEN }})
  GITHUB_REPOSITORY  owner/name, e.g. "owner/repo"
  CHECK_REPORT       optional path to the report (default: data/check_report.json)
"""
import json
import os
import sys
import urllib.error
import urllib.request

ISSUE_TITLE = "Data Quality Monitor"


def api(repo: str, token: str, path: str, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def find_open_issue(repo: str, token: str):
    page = 1
    while page <= 10:
        issues = api(repo, token, f"/issues?state=open&per_page=100&page={page}")
        for it in issues:
            if it.get("title") == ISSUE_TITLE and "pull_request" not in it:
                return it
        if len(issues) < 100:
            break
        page += 1
    return None


def build_body(report: dict) -> str:
    issues = report.get("issues", [])
    lines = [
        f"Last audit: `{report.get('generated_at', 'unknown')}`",
        f"Stages: `{' '.join(report.get('stages', []))}`",
        "",
        f"Errors: {report.get('error_count', 0)} | Warnings: {report.get('warning_count', 0)}",
        "",
    ]
    if not issues:
        lines.append("No issues found. Closing.")
        return "\n".join(lines)
    lines.append("<details><summary>Findings</summary>")
    lines.append("")
    lines.append("| Severity | Finding |")
    lines.append("| --- | --- |")
    for it in issues:
        msg = it.get("message", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {it.get('severity', 'info')} | {msg} |")
    lines.append("</details>")
    return "\n".join(lines)


def main():
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not token or not repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.")
        sys.exit(1)
    report_path = os.environ.get("CHECK_REPORT", "data/check_report.json")
    try:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not read report {report_path}: {e}")
        sys.exit(2)

    clean = not report.get("issues")
    existing = find_open_issue(repo, token)
    body = build_body(report)

    if clean:
        if existing:
            api(repo, token, f"/issues/{existing['number']}", "PATCH",
                {"state": "closed", "state_reason": "completed"})
            print(f"Closed issue #{existing['number']} (clean).")
        else:
            print("No issues found and no open monitor issue.")
        return

    if existing:
        api(repo, token, f"/issues/{existing['number']}", "PATCH",
            {"state": "open", "body": body})
        print(f"Updated issue #{existing['number']} with {len(report['issues'])} findings.")
    else:
        created = api(repo, token, "/issues", "POST",
                      {"title": ISSUE_TITLE, "body": body})
        print(f"Opened issue #{created['number']} with {len(report['issues'])} findings.")


if __name__ == "__main__":
    main()
