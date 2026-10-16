"""A bounded CSV parser with Decimal totals and a Jinja results page."""
import csv
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import PurePath

from email_validator import EmailNotValidError, validate_email
from flask import render_template, request
from werkzeug.utils import secure_filename

from core import Problem, attachment
from flaskweb import create_base


def summarize(data: bytes):
    if len(data) > 128 * 1024:
        raise Problem('CSV files must be at most 128 KiB.', 413)
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise Problem('Save the CSV as UTF-8 before uploading it.') from error
    try:
        reader = csv.reader(StringIO(text, newline=''), strict=True)
        headers = next(reader, [])
        if not headers or len(headers) > 50 or len(set(headers)) != len(headers) or any(not h.strip() or len(h) > 100 for h in headers):
            raise Problem('Use 1–50 distinct, non-empty column names, at most 100 characters each.')
        totals = [Decimal(0) for _ in headers]
        numeric = [True for _ in headers]
        present = [False for _ in headers]
        count = 0
        for row in reader:
            if not row:
                continue
            count += 1
            if count > 1000:
                raise Problem('This demo accepts at most 1,000 data rows.')
            if len(row) != len(headers):
                raise Problem(f'Data row {count} has a different number of cells than the header.')
            for index, cell in enumerate(row):
                cell = cell.strip()
                if not cell:
                    continue
                present[index] = True
                try:
                    if len(cell) > 30:
                        raise InvalidOperation
                    value = Decimal(cell)
                    if not value.is_finite() or abs(value) > Decimal('1e12') or value.as_tuple().exponent < -6:
                        raise InvalidOperation
                    totals[index] += value
                except InvalidOperation:
                    numeric[index] = False
        return {'rows': count, 'columns': len(headers), 'totals': {name: format(totals[i], 'f') for i, name in enumerate(headers) if numeric[i] and present[i]}}
    except csv.Error as error:
        raise Problem('The CSV contains malformed quoting or an oversized field.') from error


def summary_csv(summary):
    """Totals as a CSV file; column names that a spreadsheet would run as formulas are prefixed with a quote."""
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['column', 'total'])
    for name, total in summary['totals'].items():
        writer.writerow(["'" + name if name.lstrip(' ')[:1] in ('=', '+', '-', '@', '\t', '\r') else name, total])
    return output.getvalue().encode()


def summary_filename(upload_name):
    stem = secure_filename(PurePath(upload_name or '').stem)[:60]
    return f'{stem or "csv"}-summary.csv'


def create_app(settings=None, mailer=None):
    app = create_base('CSV Summary', settings, mailer)

    @app.post('/summaries')
    def upload():
        file = request.files.get('csv')
        if file is None:
            raise Problem('Choose a CSV file to summarize.')
        try:
            recipient = validate_email(request.form.get('email', ''), check_deliverability=False).normalized
        except EmailNotValidError as error:
            raise Problem('Enter a valid email address for the summary.') from error
        summary = summarize(file.read(128 * 1024 + 1))
        details = '\n'.join(f'{column}: {total}' for column, total in summary['totals'].items()) or 'No fully numeric columns.'
        message = {
            'to': recipient, 'subject': 'Your CSV summary',
            'text': f"Rows: {summary['rows']}\nColumns: {summary['columns']}\n\nNumeric totals\n{details}",
        }
        if summary['totals']:
            filename = summary_filename(file.filename)
            message['text'] += f'\n\nThe totals are attached as {filename}.'
            message['attachment'] = attachment(summary_csv(summary), filename, 'text/csv')
        result = app.extensions['store'].save(summary, message, request.remote_addr or 'shared-client')
        return render_template('index.html', title='CSV Summary', result=result, summary=summary), 201

    return app
