"""Start the app locally, or use --production for the production server."""
import argparse
import os
from dotenv import load_dotenv

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--production', action='store_true')
    options = parser.parse_args()
    load_dotenv(override=False)
    if options.production:
        os.environ['APP_ENV'] = 'production'
    host, port = os.getenv('HOST', '127.0.0.1'), int(os.getenv('PORT', '8000'))
    from app import create_app
    app = create_app()
    if options.production:
        from waitress import serve
        serve(app, host=host, port=port)
    else:
        app.run(host=host, port=port, debug=False)
