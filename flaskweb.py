"""Flask request limits and an operator view for saved email states."""
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from core import Problem, Settings, Store


def create_base(title, settings=None, mailer=None, private=False):
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=160 * 1024, MAX_FORM_MEMORY_SIZE=160 * 1024, MAX_FORM_PARTS=10)
    app.extensions['settings'] = settings
    app.extensions['store'] = Store(settings, mailer)

    @app.before_request
    def protect():
        if private and request.path != '/health':
            settings.authorize(request.headers.get('Authorization', ''))
        if request.method == 'POST':
            settings.check_origin(request.headers.get('Origin'))
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise Problem('Cross-site submissions are not allowed.', 403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'})
        return response

    @app.errorhandler(Problem)
    def problem(error):
        response = app.make_response((render_template('index.html', title=title, error=str(error)), error.status))
        if error.status == 401:
            response.headers['WWW-Authenticate'] = 'Basic realm="Operator"'
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return problem(Problem('Upload is too large for this demo.', 413))

    @app.get('/health')
    def health():
        return jsonify(status='ok')

    @app.get('/admin')
    def admin():
        settings.authorize(request.headers.get('Authorization', ''))
        return render_template('admin.html', title=title, records=app.extensions['store'].records())

    @app.get('/')
    def home():
        return render_template('index.html', title=title)

    return app
