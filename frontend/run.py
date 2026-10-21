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

    # `allow_unsafe_werkzeug` lets the Flask development server serve
    # WebSocket connections through simple-websocket.
    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug,
        allow_unsafe_werkzeug=True,
    )
