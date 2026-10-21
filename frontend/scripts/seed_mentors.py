"""Seed the mentor directory with eight clearly fake mentors.

The accounts are obviously fake (``@example.test``), the password is read
from the ``SEED_MENTOR_PASSWORD`` environment variable and is **never**
printed, and the script is idempotent: running it twice does not create
duplicates.

Usage::

    SEED_MENTOR_PASSWORD=choose-a-password python scripts/seed_mentors.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User  # noqa: E402
from mentorship_models import MentorProfile  # noqa: E402

# --- The demo mentors -------------------------------------------------------
MENTORS = [
    {
        "name": "Aditi Rao",
        "headline": "Backend engineer who builds payment systems",
        "bio": "I work on high-throughput payment services and enjoy helping "
               "students turn a vague idea into a working API.",
        "expertise": "python, flask, sql, api design, docker",
        "years_experience": 6,
        "company": "Northwind Payments",
        "timezone": "IST (UTC+5:30)",
        "availability": "Weekday evenings after 19:00 IST, weekends any time",
    },
    {
        "name": "Ben Okafor",
        "headline": "Full stack developer, React and Django",
        "bio": "I ship small full stack products. Happy to review your project "
               "structure and your database schema.",
        "expertise": "javascript, react, django, html, css, git",
        "years_experience": 4,
        "company": "Studio Kettle",
        "timezone": "GMT (UTC+0)",
        "availability": "Saturday mornings",
    },
    {
        "name": "Chandra Menon",
        "headline": "Data analyst turning messy spreadsheets into dashboards",
        "bio": "I have built reporting pipelines for retail and logistics. I "
               "can help you learn SQL properly and stop fearing pandas.",
        "expertise": "sql, pandas, numpy, excel, power bi, statistics",
        "years_experience": 7,
        "company": "Meridian Retail Group",
        "timezone": "IST (UTC+5:30)",
        "availability": "Tuesday and Thursday evenings",
    },
    {
        "name": "Diego Fuentes",
        "headline": "DevOps engineer, containers and CI",
        "bio": "I automate deployments. If you are stuck on Docker or GitHub "
               "Actions, that is exactly the kind of thing I enjoy untangling.",
        "expertise": "docker, kubernetes, git, linux, ci cd, bash",
        "years_experience": 5,
        "company": "Harbour Cloud",
        "timezone": "CET (UTC+1)",
        "availability": "Wednesday afternoons",
    },
    {
        "name": "Elena Petrova",
        "headline": "Python mentor for absolute beginners",
        "bio": "I teach Python to people who have never programmed. Expect "
               "patience, small steps and lots of debugging together.",
        "expertise": "python, oop, file handling, git",
        "years_experience": 3,
        "company": "Self-employed",
        "timezone": "EET (UTC+2)",
        "availability": "Daily, 17:00-19:00 EET",
    },
    {
        "name": "Farhan Iqbal",
        "headline": "Database engineer, modelling and query tuning",
        "bio": "I spend my days making slow queries fast and messy schemas "
               "sane. Bring me your ER diagram.",
        "expertise": "sql, mysql, postgresql, database modeling, python",
        "years_experience": 9,
        "company": "Ledgerworks",
        "timezone": "PKT (UTC+5)",
        "availability": "Friday afternoons",
    },
    {
        "name": "Grace Tan",
        "headline": "Security engineer, web application security",
        "bio": "I break things so they do not need to be broken later. I run "
               "the security track of our campus CTF.",
        "expertise": "python, linux, networking, cryptography",
        "years_experience": 6,
        "company": "Aegis Security",
        "timezone": "SGT (UTC+8)",
        "availability": "Late nights, weekends",
    },
    {
        "name": "Hassan Youssef",
        "headline": "Machine learning engineer, NLP in production",
        "bio": "I take models from a notebook into production. Ask me about "
               "evaluation, drift, or why your accuracy looks too good.",
        "expertise": "python, machine learning, nlp, pandas, docker, aws",
        "years_experience": 5,
        "company": "Lumen AI",
        "timezone": "EET (UTC+2)",
        "availability": "Sunday mornings",
    },
]


def main():
    password = os.environ.get("SEED_MENTOR_PASSWORD")
    if not password:
        print(
            "SEED_MENTOR_PASSWORD is not set.\n"
            "Set it to the password you want the demo mentors to use, for example:\n"
            "    SEED_MENTOR_PASSWORD=choose-a-password python scripts/seed_mentors.py",
            file=sys.stderr,
        )
        return 1

    if len(password) < 8:
        print("SEED_MENTOR_PASSWORD must be at least 8 characters.", file=sys.stderr)
        return 1

    app = create_app()
    created = 0
    updated = 0

    with app.app_context():
        for index, mentor in enumerate(MENTORS):
            email = f"mentor{index + 1}@example.test"
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(name=mentor["name"], email=email)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                created += 1
            else:
                updated += 1

            profile = MentorProfile.query.filter_by(user_id=user.id).first()
            if profile is None:
                profile = MentorProfile(user_id=user.id)
                db.session.add(profile)

            profile.headline = mentor["headline"]
            profile.bio = mentor["bio"]
            profile.expertise = mentor["expertise"]
            profile.years_experience = mentor["years_experience"]
            profile.company = mentor["company"]
            profile.timezone = mentor["timezone"]
            profile.availability = mentor["availability"]
            profile.max_mentees = 3
            profile.is_active = True

        db.session.commit()

    print(f"Seed complete: {created} mentors created, {updated} already existed.")
    print("Sign in with any mentor1@example.test … mentor8@example.test")
    print("The password is the value of SEED_MENTOR_PASSWORD (it was not printed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
