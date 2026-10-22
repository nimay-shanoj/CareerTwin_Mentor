"""CareerTwin – development / production entrypoint.

Usage:
    python run.py                 # http://127.0.0.1:5000
    HOST=0.0.0.0 PORT=8080 python run.py

``socketio.run`` is used instead of ``app.run`` so that the WebSocket transport
is available in development.

Async mode
----------
Development uses the ``threading`` async mode together with the
``simple-websocket`` package.  It needs no extra system-level dependency
(``eventlet``/``gevent`` are not installable on every platform) and is more than
fast enough for a single-process demo.

Production uses ``gevent`` (see ``Procfile``) because a single gevent worker
handles thousands of concurrent WebSocket connections on one core.
"""

import os

from app import create_app
from extensions import socketio

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = app.config.get("DEBUG", False)

    # The auto-reloader runs the app in a child process, which needs a console
    # it can talk to. That makes the server impossible to start detached
    # (and doubles the number of processes to keep track of), so it is off by
    # default. Set FLASK_RELOAD=1 to switch it on while editing templates.
    use_reloader = os.getenv("FLASK_RELOAD", "0") == "1"

    # `allow_unsafe_werkzeug` lets the Flask development server serve
    # WebSocket connections through simple-websocket.
    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug,
        use_reloader=use_reloader,
        allow_unsafe_werkzeug=True,
    )
