"""Flask extension singletons.

Kept in one place so blueprints, the app factory and the Socket.IO handlers all
share the same objects.  Nothing here is configured at import time – every
setting is applied inside the app factory.
"""

from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

# ``async_mode`` and CORS origins are supplied by the app factory via
# ``init_app``; the defaults here are only a safety net.
socketio = SocketIO(async_mode="threading", cors_allowed_origins=[])
