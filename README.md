# Visa Monitor

Lightweight GitHub Actions monitor for Schengen appointment pages used from Morocco.

## What it checks

- France: TLScontact Casablanca
- Spain: BLS Casablanca
- Germany: TLScontact Rabat
- Italy: VFS Global Casablanca

## How it works

- GitHub Actions runs `monitor.py` every 15 minutes
- the script checks each source once per workflow run
- Telegram is used only for alerts
- no infinite loop is used inside GitHub Actions

## Required GitHub Secrets

- `TG_TOKEN`
- `TG_CHAT_ID`

## Local run

```bash
pip install -r requirements.txt
python monitor.py
```

## Notes

- `BLS Spain Casablanca` public pages do not always expose live slot state clearly without the booking flow, so this source may return `unknown` instead of a misleading `available`.
- API behavior can change over time. When an API fails, the script falls back to HTML inspection when possible.
