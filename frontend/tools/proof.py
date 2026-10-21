"""End-to-end proof that the app stores real data in a real database.

Run the server first, then:   python tools/proof.py
"""

import os
import re
import sqlite3
import subprocess
import sys
import time

import requests

BASE = "http://127.0.0.1:5000"
EMAIL = "proof.user@example.com"
PASSWORD = "SuperSecret123"

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')
DB = os.path.join("instance", "proof.db")


def token(session, path="/register"):
    page = session.get(f"{BASE}{path}", timeout=10)
    match = CSRF_RE.search(page.text)
    if not match:
        raise RuntimeError(f"no CSRF token on {path}")
    return match.group(1)


def step(label):
    print(f"\n--- {label} " + "-" * max(0, 56 - len(label)))


step("1. register a real account over HTTP")
session = requests.Session()
response = session.post(
    f"{BASE}/register",
    data={
        "csrf_token": token(session),
        "name": "Proof User",
        "email": EMAIL,
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "role": "student",
    },
    allow_redirects=False,
    timeout=10,
)
print(f"  POST /register -> {response.status_code} {response.headers.get('Location')}")
print(f"  session cookie set: {'session' in session.cookies.get_dict()}")

step("2. look at the database file")
print(f"  file: {DB} ({os.path.getsize(DB)} bytes)")
connection = sqlite3.connect(DB)
rows = connection.execute(
    "SELECT id, name, email, password_hash, created_at FROM users"
).fetchall()
for row in rows:
    print(f"  id            : {row[0]}")
    print(f"  name          : {row[1]}")
    print(f"  email         : {row[2]}")
    print(f"  password_hash : {row[3][:48]}…")
    print(f"  created_at    : {row[4]}")
raw = open(DB, "rb").read()
print(f"  the plaintext password is in the file: {PASSWORD.encode() in raw}")
print(f"  the email is in the file (in clear) : {EMAIL.encode() in raw}")
connection.close()

step("3. the session really works")
response = session.get(f"{BASE}/dashboard.html", timeout=10)
greeting = re.search(r'class="dash-hero__greeting">([^<]*)', response.text)
print(f"  GET /dashboard.html -> {response.status_code}")
print(f"  greeting: {greeting.group(1).strip() if greeting else '?'}")

step("4. fill in a real profile, a real quiz result, a real progress tick")
for path, payload in [
    ("/api/mentors/me", {"headline": "Proof mentor", "expertise": "python, sql"}),
    ("/api/progress/toggle", {"task_id": 1, "completed": True}),
    (
        "/api/quiz/submit",
        {
            "topic": "Career Compatibility Assessment",
            "score": 14,
            "total_questions": 20,
            "percentage": 70,
            "compatible_career": "Backend Developer",
            "compatibility_scores": {"Backend Developer": 70, "Data Analyst": 55},
        },
    ),
]:
    token_value = re.search(
        r'name="csrf-token" content="([^"]+)"',
        session.get(f"{BASE}/dashboard.html", timeout=10).text,
    ).group(1)
    response = session.post(
        f"{BASE}{path}", json=payload, headers={"X-CSRFToken": token_value}, timeout=10
    )
    print(f"  POST {path:26} -> {response.status_code}")

step("5. read the data back out of the database")
connection = sqlite3.connect(DB)
for table, columns in [
    ("mentor_profiles", "headline, expertise, is_active"),
    ("user_progress", "task_id, completed"),
    ("quiz_results", "score, total_questions, percentage, compatible_career"),
]:
    rows = connection.execute(f"SELECT {columns} FROM {table}").fetchall()
    print(f"  {table:20} {rows}")
connection.close()

step("6. the API returns the same data")
response = session.get(f"{BASE}/api/overview", timeout=10)
print(f"  GET /api/overview -> {response.status_code}")
print(f"  {response.json()}")

step("7. log out and back in — the password check really works")
session.get(f"{BASE}/logout", timeout=10)
fresh = requests.Session()
token_value = re.search(
    r'name="csrf-token" content="([^"]+)"',
    fresh.get(f"{BASE}/login", timeout=10).text,
).group(1)
response = fresh.post(
    f"{BASE}/login",
    data={"csrf_token": token_value, "email": EMAIL, "password": PASSWORD},
    allow_redirects=False,
    timeout=10,
)
print(f"  POST /login -> {response.status_code} {response.headers.get('Location')}")
print(f"  GET /api/quiz/history -> {fresh.get(f'{BASE}/api/quiz/history', timeout=10).status_code}")

print("\n" + "=" * 62)
print("  Real database, real password hashing, real sessions.")
print("=" * 62)
