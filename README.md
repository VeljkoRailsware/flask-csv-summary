# Flask CSV Summary: Upload a CSV and Email the Totals (Mailtrap Email API)

A small Python 3.12 Flask app: upload a CSV, see the row count, column count and the total of every numeric column, and get the same report by email with the totals attached as a CSV file. Emails go through the [Mailtrap Email API](https://mailtrap.io/email-api/?utm_source=github&utm_medium=repo&utm_campaign=flask-csv-summary) using only the standard library's `urllib.request` (no SDK). During development they are captured in the [Mailtrap Email Sandbox](https://mailtrap.io/email-sandbox/?utm_source=github&utm_medium=repo&utm_campaign=flask-csv-summary), so nothing reaches a real inbox.

## Technologies

- Flask 3 and Waitress
- Python 3.12, `csv` module and `Decimal` arithmetic
- SQLite for requests and the email outbox
- [Mailtrap Email API](https://docs.mailtrap.io/email-api-smtp/overview?utm_source=github&utm_medium=repo&utm_campaign=flask-csv-summary) for sending, [Mailtrap Email Sandbox](https://docs.mailtrap.io/email-sandbox/overview?utm_source=github&utm_medium=repo&utm_campaign=flask-csv-summary) for testing, called with stdlib `urllib.request`

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

Upload examples/sales.csv with an email address. The page displays three rows, three columns, a quantity total of 6 and an amount total of 36.50, and emails that same report with `sales-summary.csv` attached:

```csv
column,total
quantity,6
amount,36.50
```

Blank cells are ignored; a mixed text/numeric column is not totaled. When no column is fully numeric, the email has no attachment.

## How it works

`app.py` uses Python’s strict CSV reader and `Decimal` arithmetic. Jinja renders the resulting totals with automatic escaping. The original upload is processed in memory, not retained.

`app.py` also builds the attachment. Column names that start with `=`, `+`, `-` or `@` get a leading `'`, so a spreadsheet opens them as text instead of running them as formulas (CSV injection).

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

## FAQ

### How do I send an email with a CSV attachment from Flask using the Mailtrap Email API?

Base64-encode the file and add it to the `attachments` array of a `POST` to the Mailtrap Email API. `core.py` does this with `urllib.request`, a 15-second timeout and a check that the response contains a message ID:

```python
import base64, json, os
from urllib.request import Request, urlopen

body = {
    'from': {'email': os.environ['MAIL_FROM']},
    'to': [{'email': 'reader@example.com'}],
    'subject': 'Your CSV summary',
    'text': 'Rows: 3\nColumns: 3',
    'category': 'csv.summary',
    'attachments': [{
        'content': base64.b64encode(b'column,total\r\namount,36.50\r\n').decode(),
        'filename': 'sales-summary.csv',
        'type': 'text/csv',
        'disposition': 'attachment',
    }],
}
request = Request('https://send.api.mailtrap.io/api/send', data=json.dumps(body).encode(), method='POST',
                  headers={'Content-Type': 'application/json',
                           'Authorization': f"Bearer {os.environ['MAILTRAP_PRODUCTION_TOKEN']}"})
with urlopen(request, timeout=15) as response:
    print(json.loads(response.read()))  # {'success': True, 'message_ids': [...]}
```

### How do I test Flask emails without sending them to real people?

Keep `MAIL_MODE=sandbox`. The app then sends to `https://sandbox.api.mailtrap.io/api/send/{inbox_id}` and the [Mailtrap Email Sandbox](https://mailtrap.io/email-sandbox/?utm_source=github&utm_medium=repo&utm_campaign=flask-csv-summary) captures each message, so you can check the HTML, text and attachment without delivering anything. For offline work, `MAIL_MODE=log` with `APP_ENV=development` writes `.eml` files to `.data/mail-preview/` instead. See [email setup](docs/email-setup.md).

### How do I keep a generated CSV from running formulas in Excel or Google Sheets?

Spreadsheets treat a cell that starts with `=`, `+`, `-` or `@` as a formula. Uploaded column names are user input, so `summary_csv()` in `app.py` prefixes those with `'` before writing the attachment. Numeric totals are written as plain numbers.

### What happens if the email fails to send?

The summary request and its email are saved in one SQLite transaction before sending, so a failure loses nothing. `/admin` shows each email's state, and `python retry_email.py` retries pending and failed messages. Timeouts stay `unknown` and are not retried automatically, because the first attempt may already have been accepted.

## License

MIT; see [LICENSE](LICENSE).
