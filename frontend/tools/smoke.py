"""Full smoke test: every page, every API endpoint, and the error paths.

Run with:  python tools/smoke.py
It starts the app in-process, exercises the whole surface and prints a
report. It exits non-zero if anything fails.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SECRET_KEY", "smoke-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("TCP_CHAT_ENABLED", "0")

from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from extensions import db  # noqa: E402

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')

PUBLIC_PAGES = [
    "/",
    "/landing.html",
    "/login",
    "/login.html",
    "/register",
    "/register.html",
    "/healthz",
]

PROTECTED_PAGES = [
    "/dashboard",
    "/dashboard.html",
    "/overview",
    "/overview.html",
    "/profile",
    "/profile.html",
    "/resume",
    "/resume.html",
    "/roadmap",
    "/roadmap.html",
    "/skillgap",
    "/skillgap.html",
    "/projects",
    "/projects.html",
    "/quiz",
    "/quiz.html",
    "/progress",
    "/progress.html",
    "/mentors",
    "/mentors.html",
    "/mentors/become",
    "/mentorship",
    "/mentorship.html",
    "/messages",
    "/messages.html",
    "/network",
    "/network.html",
]

READ_APIS = [
    "/api/overview",
    "/api/progress",
    "/api/careers",
    "/api/mentors",
    "/api/conversations",
    "/api/notifications/summary",
    "/api/network/stats",
    "/api/resume/latest",
    "/api/quiz/history",
]

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


def main():
    class SmokeConfig(Config):
        TESTING = True
        SECRET_KEY = "smoke-secret"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        WTF_CSRF_ENABLED = False
        TCP_CHAT_ENABLED = False
        SERVER_NAME = "localhost"

    app = create_app(SmokeConfig)
    with app.app_context():
        db.create_all()

    client = app.test_client()

    print("\n== public pages ==")
    for path in PUBLIC_PAGES:
        response = client.get(path)
        check(f"GET {path}", response.status_code == 200, response.status_code)

    print("\n== protected pages redirect anonymous users ==")
    for path in PROTECTED_PAGES:
        response = client.get(path)
        check(
            f"GET {path} -> 302",
            response.status_code == 302 and "login" in response.headers.get("Location", ""),
            response.status_code,
        )

    print("\n== register ==")
    token = CSRF_RE.search(client.get("/register").get_data(as_text=True)).group(1)
    response = client.post(
        "/register",
        data={
            "csrf_token": token,
            "name": "Smoke Tester",
            "email": "smoke@example.test",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    check("POST /register -> 302", response.status_code == 302, response.status_code)

    print("\n== protected pages render for a signed-in user ==")
    for path in PROTECTED_PAGES:
        response = client.get(path)
        check(f"GET {path} -> 200", response.status_code == 200, response.status_code)

    print("\n== read APIs ==")
    for path in READ_APIS:
        response = client.get(path)
        check(f"GET {path} -> 200", response.status_code == 200, response.status_code)
        if response.status_code == 200:
            check(
                f"GET {path} is JSON",
                response.is_json,
                response.headers.get("Content-Type"),
            )

    print("\n== write APIs ==")
    dashboard = client.get("/dashboard").get_data(as_text=True)
    token = CSRF_RE.search(dashboard).group(1)
    writes = [
        ("/api/progress/toggle", {"task_id": 3, "completed": True}),
        (
            "/api/quiz/submit",
            {
                "topic": "Career Compatibility Assessment",
                "score": 12,
                "total_questions": 20,
                "percentage": 62,
                "compatible_career": "Backend Developer",
                "compatibility_scores": {"Backend Developer": 62},
            },
        ),
        ("/api/progress/reset", {}),
        ("/api/mentors/me", {"headline": "Smoke mentor", "expertise": "python, sql"}),
        ("/api/chat/token", {}),
    ]
    for path, payload in writes:
        response = client.post(path, json=payload, headers={"X-CSRFToken": token})
        check(
            f"POST {path} -> 2xx",
            200 <= response.status_code < 300,
            response.status_code,
        )

    print("\n== the mentor workflow ==")
    # The directory never lists the person who is asking, so a second mentor
    # is needed to see anything in it.
    other = app.test_client()
    other_token = CSRF_RE.search(other.get("/register").get_data(as_text=True)).group(1)
    other.post(
        "/register",
        data={
            "csrf_token": other_token,
            "name": "Second Mentor",
            "email": "smoke2@example.test",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    other.post(
        "/api/mentors/me",
        json={"headline": "Second mentor", "expertise": "python, sql", "max_mentees": 2},
    )
    response = client.post(
        "/api/mentors/me",
        json={"headline": "Smoke mentor", "expertise": "python, sql"},
    )
    check("POST /api/mentors/me", response.status_code == 200, response.status_code)

    listing = client.get("/api/mentors").get_json()
    check("the directory lists the other mentor", listing["total"] >= 1, listing)
    if listing["mentors"]:
        check("the match score is present", "match" in listing["mentors"][0])
        check(
            "the match reasons are present",
            bool(listing["mentors"][0].get("match_reasons")),
        )

    other_user_id = None
    with app.app_context():
        from models import User

        user = User.query.filter_by(email="smoke2@example.test").first()
        other_user_id = user.id if user else None

    response = client.post(
        "/api/mentorship/requests",
        json={"mentor_id": other_user_id, "message": "Please help me"},
    )
    check("POST /api/mentorship/requests", response.status_code == 201, response.status_code)
    request_id = response.get_json()["request"]["id"] if response.is_json else None

    incoming = other.get("/api/mentorship/requests?box=incoming").get_json()
    check(
        "the request shows up in the mentor's inbox",
        any(r["id"] == request_id for r in incoming["requests"]),
        incoming,
    )

    response = other.post(f"/api/mentorship/requests/{request_id}/accept")
    check("POST accept", response.status_code == 200, response.status_code)

    conversations = client.get("/api/conversations").get_json()["conversations"]
    check("accepting created a conversation", len(conversations) == 1, conversations)
    if conversations:
        conversation_id = conversations[0]["id"]
        response = client.get(f"/api/conversations/{conversation_id}/messages")
        check("GET the messages", response.status_code == 200, response.status_code)
        response = other.get(f"/api/conversations/{conversation_id}/messages")
        check(
            "the other participant can read them too",
            response.status_code == 200,
            response.status_code,
        )

    print("\n== error paths ==")
    response = client.get("/api/conversations/999999/messages")
    check("GET an unknown conversation -> 404", response.status_code == 404, response.status_code)
    response = client.get("/nope")
    check("GET an unknown page -> 404", response.status_code == 404, response.status_code)
    response = client.post("/api/mentorship/requests", json={"mentor_id": 999999})
    check(
        "POST a request to an unknown mentor -> 404",
        response.status_code == 404,
        response.status_code,
    )
    response = client.post(
        "/api/mentorship/requests", json={"mentor_id": 1}
    )
    check(
        "POST a request to yourself -> 400",
        response.status_code == 400 and response.get_json()["code"] == "SELF_REQUEST",
        response.status_code,
    )

    print("\n== the design system ==")
    css_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "css"
    )
    root_selectors = 0
    for root, _dirs, files in os.walk(css_dir):
        for name in files:
            if not name.endswith(".css"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                text = handle.read()
            # Strip the comments, then count the actual selectors.
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            root_selectors += len(re.findall(r"(?m)^\s*:root\s*\{", text))
    check("exactly one :root block in the CSS", root_selectors == 1, root_selectors)

    with app.app_context():
        tables = sorted(db.metadata.tables.keys())
    check(
        "the new tables exist",
        {"mentor_profiles", "mentorship_requests", "conversations", "messages"}
        <= set(tables),
        tables,
    )
    check(
        "the original tables are untouched",
        {"users", "profiles", "quiz_results", "user_progress", "resumes"}
        <= set(tables),
        tables,
    )

    print("\n" + "=" * 56)
    print(f"  {checks - len(failures)} / {checks} checks passed")
    if failures:
        print("  failures:")
        for failure in failures:
            print("   -", failure)
    print("=" * 56)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
