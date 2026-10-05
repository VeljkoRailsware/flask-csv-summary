# CSV Summary

Count rows and columns, then add up the columns that contain only numeric values. This app demonstrates file parsing and server-rendered reports.

## Technologies

- flask
- waitress
- Python 3.12
- SQLite
- stdlib urllib.request

## Start locally

Use Python 3.12, the version used for the included checks. Commands below assume a POSIX shell; on Windows activate `.venv\Scripts\activate` instead.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -c constraints.txt
cp .env.example .env
# Edit .env using the comments beside each variable.
python run.py
```

Open http://localhost:8000. Set your own `ADMIN_PASSWORD` (16 or more random characters); the operator username is `admin`. The form is public; /admin requires operator authentication. Credentials are supplied by whoever runs the project and are never included in this repository.

For an immediate local trial, use `MAIL_MODE=log` and `APP_ENV=development`. The full message, including any attachment, is saved as an `.eml` file under `.data/mail-preview/`; no email is sent. Keep these files private. The default configuration uses a sandbox. [Email setup](docs/email-setup.md) explains both sandbox and live sending.

## Try it

Upload examples/sales.csv with an email address. The page displays three rows, three columns, a quantity total of 6 and an amount total of 36.50, and emails that same report. Blank cells are ignored; a mixed text/numeric column is not totaled.

## How it works

`app.py` uses Python’s strict CSV reader and `Decimal` arithmetic. Jinja renders the resulting totals with automatic escaping. The original upload is processed in memory, not retained.

`core.py` stores the request and intended email in one SQLite transaction before attempting the HTTP API. `/admin` shows request references and send states without publishing submitted content. Server-side credentials never appear in the page or API schema.

All form submissions have origin checks. Non-browser clients can omit Origin; public API calls still have rate and size limits. Each client is limited to 12 requests per hour and each recipient to 3. Forwarded IP headers are ignored; behind a reverse proxy, clients may share a limit. Add infrastructure-level limits before handling significant public traffic.

## Email states and retries

`accepted` means the email API accepted the message, not that it reached the inbox. `logged` is only a local preview. `failed` leaves the saved request intact; fix configuration and run:

```sh
python retry_email.py
```

The command retries pending messages and known failures. A connection loss while sending is `unknown`, and an interrupted process can leave a message `sending`. Neither state is automatically retried: inspect provider logs before manually reconciling it. There is no delivery-webhook integration.

## Tests and production startup

```sh
python -m pip install -r requirements-dev.txt -c constraints.txt
python -m pytest -q
python run.py --production
```

Tests use local mail doubles or preview files. Actual sending is optional and requires the operator’s own credentials; it is not a prerequisite for publishing this source. Runtime and test versions are pinned in the requirement files and `constraints.txt`. CI runs these checks on Python 3.12.

Production uses Waitress. Put it behind HTTPS, set `APP_URL` to the public origin, and keep `DB_PATH` on a persistent private volume. Use one application process for this demo. Basic authentication must be protected by HTTPS outside localhost. `--production` disables log-mode sending even if it remains selected in `.env`.

## Scope

Comma-separated UTF-8 input only, at most 1,000 data rows and 50 columns. Numeric totals accept finite values up to 1e12 with at most six decimal places. Dates, currency symbols, and localized separators are treated as text. Stored request data and mail previews have no automatic retention cleanup. No database service, scheduler, Redis, or external account is required for local tests.

## License

MIT; see [LICENSE](LICENSE).
