"""Walk the whole student + mentor journey over HTTP.

Student signs up -> browses the directory -> requests a mentor.
Mentor signs up -> completes the mentor profile -> accepts the request.
Both end up in the same conversation.
"""

import os
import re
import sys
import time

import requests

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')

# Unique addresses per run, so the script can be run repeatedly.
RUN = str(int(time.time()))
STUDENT_EMAIL = f"sana-{RUN}@example.test"
MENTOR_EMAIL = f"ravi-{RUN}@example.test"


def token(session, path="/register"):
    page = session.get(f"{BASE}{path}", timeout=10)
    match = CSRF_RE.search(page.text)
    if not match:
        raise RuntimeError(f"no CSRF token on {path}")
    return match.group(1)


def register(session, name, email, role):
    response = session.post(
        f"{BASE}/register",
        data={
            "csrf_token": token(session),
            "name": name,
            "email": email,
            "password": "password123",
            "confirm_password": "password123",
            "role": role,
        },
        timeout=10,
        allow_redirects=False,
    )
    return response


def login(session, path, email):
    return session.post(
        f"{BASE}{path}",
        data={
            "csrf_token": token(session, path),
            "email": email,
            "password": "password123",
        },
        timeout=10,
        allow_redirects=False,
    )


def api_post(session, path, payload):
    """POST JSON with the CSRF header, the way the browser client does."""
    page = session.get(f"{BASE}/dashboard.html", timeout=10)
    match = CSRF_RE.search(page.text)
    return session.post(
        f"{BASE}{path}",
        json=payload,
        headers={"X-CSRFToken": match.group(1) if match else ""},
        timeout=10,
    )


def ok(label, condition, detail=""):
    mark = "ok  " if condition else "FAIL"
    print(f"  {mark} {label} {detail}")
    if not condition:
        raise SystemExit(1)


print("\n== the student ==")
student = requests.Session()
r = register(student, "Sana Student", STUDENT_EMAIL, "student")
ok("signs up as a student", r.status_code == 302, r.headers.get("Location"))
ok("lands on the dashboard", r.headers.get("Location", "").endswith("/dashboard.html"))

# The directory may already hold mentors from a previous run; remember how
# many so the "draft is hidden" check below is meaningful.
before = student.get(f"{BASE}/api/mentors", timeout=10).json()["total"]
print(f"       the directory holds {before} mentor(s)")

print("\n== the mentor ==")
mentor = requests.Session()
r = register(mentor, "Ravi Mentor", MENTOR_EMAIL, "mentor")
ok("signs up as a mentor", r.status_code == 302, r.headers.get("Location"))
ok("lands on the mentor onboarding", "/mentors/become" in r.headers.get("Location", ""))

# A draft profile must not be visible to the student yet.
listing = student.get(f"{BASE}/api/mentors", timeout=10).json()
ok(
    "the draft is hidden from the directory",
    listing["total"] == before,
    (before, listing["total"]),
)

# The mentor completes the profile.
r = api_post(
    mentor,
    "/api/mentors/me",
    {
        "headline": "Backend engineer, six years in",
        "bio": "I help students with Flask and SQL.",
        "expertise": "python, sql, flask",
        "years_experience": 6,
        "company": "Northwind",
        "max_mentees": 3,
    },
)
ok("publishes the mentor profile", r.status_code == 200, r.status_code)

listing = student.get(f"{BASE}/api/mentors", timeout=10).json()
ok(
    "the mentor now appears in the directory",
    listing["total"] == before + 1,
    (before, listing["total"]),
)
new_mentor = [m for m in listing["mentors"] if m["email"] == MENTOR_EMAIL]
ok("and it is the new one", len(new_mentor) == 1)
if new_mentor:
    entry = new_mentor[0]
    print(f"       match {entry['match']}%  reasons: "
          f"{[x['label'] for x in entry['match_reasons']]}")
    mentor_id = entry["user_id"]
else:
    mentor_id = None

print("\n== the request ==")
r = api_post(
    student,
    "/api/mentorship/requests",
    {"mentor_id": mentor_id, "message": "Can you help me with Flask?"},
)
ok("the student sends a request", r.status_code == 201, r.status_code)
request_id = r.json()["request"]["id"]

inbox = mentor.get(f"{BASE}/api/mentorship/requests?box=incoming", timeout=10).json()
ok(
    "the request arrives in the mentor's inbox",
    any(x["id"] == request_id for x in inbox["requests"]),
    inbox,
)

r = api_post(mentor, f"/api/mentorship/requests/{request_id}/accept", {})
ok("the mentor accepts", r.status_code == 200, r.status_code)

print("\n== the conversation ==")
student_convs = student.get(f"{BASE}/api/conversations", timeout=10).json()["conversations"]
mentor_convs = mentor.get(f"{BASE}/api/conversations", timeout=10).json()["conversations"]
ok("the student has a conversation", len(student_convs) == 1, student_convs)
ok("the mentor has a conversation", len(mentor_convs) == 1, mentor_convs)
if student_convs and mentor_convs:
    ok(
        "it is the same conversation",
        student_convs[0]["id"] == mentor_convs[0]["id"],
        (student_convs[0]["id"], mentor_convs[0]["id"]),
    )
    conversation_id = student_convs[0]["id"]
    print(f"       conversation {conversation_id} with "
          f"{student_convs[0]['other_name']}")

print("\n== signing back in ==")
student2 = requests.Session()
r = login(student2, "/login", STUDENT_EMAIL)
ok("the student signs in at /login", r.status_code == 302, r.headers.get("Location"))
ok("and lands on the dashboard", r.headers.get("Location", "").endswith("/dashboard.html"))

mentor2 = requests.Session()
r = login(mentor2, "/mentor/login", MENTOR_EMAIL)
ok("the mentor signs in at /mentor/login", r.status_code == 302, r.headers.get("Location"))
ok(
    "and lands in the request inbox",
    r.headers.get("Location", "").endswith("/mentorship.html"),
    r.headers.get("Location"),
)

mentor3 = requests.Session()
r = login(mentor3, "/login", MENTOR_EMAIL)
ok("the mentor also signs in at /login", r.status_code == 302, r.headers.get("Location"))
ok(
    "and is routed to the request inbox",
    r.headers.get("Location", "").endswith("/mentorship.html"),
    r.headers.get("Location"),
)

print("\n" + "=" * 56)
print("  The student + mentor journey works end to end.")
print("=" * 56)
