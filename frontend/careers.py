"""Single source of truth for the four career pathways.

Before this module the same data lived in five different places
(``roadmap.js``, ``skillgap.html``, ``projects.html``, ``progress_routes.py``
and ``profile.html``), which made every content change a five-file edit.

Nothing here is allowed to change the ``id`` of a progress task: those ids are
referenced by rows already stored in ``user_progress``.
"""

from flask import Blueprint, jsonify

careers_bp = Blueprint("careers_bp", __name__)


# ---------------------------------------------------------------------------
# Progress tasks
#
# The ids below are a stable public contract – stored progress rows point at
# them. Never renumber, never reuse an id.
# ---------------------------------------------------------------------------
PROGRESS_TASKS = [
    {"id": 1, "goal": "Python Developer", "task": "Python Basics", "desc": "Variables, loops, conditionals"},
    {"id": 2, "goal": "Python Developer", "task": "OOP in Python", "desc": "Classes, inheritance, methods"},
    {"id": 3, "goal": "Python Developer", "task": "File Handling", "desc": "Read/write files with Python"},
    {"id": 4, "goal": "Python Developer", "task": "MySQL with Python", "desc": "Connect and query a database"},
    {"id": 5, "goal": "Python Developer", "task": "Flask Basics", "desc": "Build a simple web app"},
    {"id": 6, "goal": "Backend Developer", "task": "REST API Design", "desc": "Understand HTTP methods & endpoints"},
    {"id": 7, "goal": "Backend Developer", "task": "Django Setup", "desc": "Create a Django project"},
    {"id": 8, "goal": "Backend Developer", "task": "Authentication", "desc": "User login & JWT tokens"},
    {"id": 9, "goal": "Backend Developer", "task": "Database Modeling", "desc": "Design tables and relationships"},
    {"id": 10, "goal": "Full Stack Developer", "task": "HTML & CSS", "desc": "Build responsive web pages"},
    {"id": 11, "goal": "Full Stack Developer", "task": "JavaScript Basics", "desc": "DOM, events, fetch API"},
    {"id": 12, "goal": "Full Stack Developer", "task": "React Fundamentals", "desc": "Components, props, state"},
    {"id": 13, "goal": "Full Stack Developer", "task": "Django + React", "desc": "Connect frontend and backend"},
    {"id": 14, "goal": "Data Analyst", "task": "Python for Data", "desc": "NumPy and Pandas basics"},
    {"id": 15, "goal": "Data Analyst", "task": "SQL Querying", "desc": "SELECT, JOIN, GROUP BY"},
    {"id": 16, "goal": "Data Analyst", "task": "Data Visualization", "desc": "Matplotlib and Seaborn"},
    {"id": 17, "goal": "Data Analyst", "task": "Power BI", "desc": "Build an interactive dashboard"},
]


# ---------------------------------------------------------------------------
# Career definitions
# ---------------------------------------------------------------------------
CAREERS = [
    {
        "name": "Python Developer",
        "icon": "code",
        "blurb": "Write clean, tested Python and ship small tools and services.",
        "skills": [
            "Python", "OOP", "File Handling", "MySQL", "Flask", "Git", "APIs",
        ],
        "roadmap": [
            {"title": "Python Basics", "detail": "Variables, loops, conditionals and functions."},
            {"title": "OOP Concepts", "detail": "Classes, inheritance, encapsulation and composition."},
            {"title": "File Handling", "detail": "Reading and writing text, CSV and JSON files."},
            {"title": "MySQL", "detail": "Schema design, queries and connecting from Python."},
            {"title": "Flask / Django", "detail": "Serve a small web app with a real framework."},
            {"title": "Projects", "detail": "Ship two portfolio projects with READMEs and tests."},
        ],
        "projects": [
            {"title": "Todo CLI App", "desc": "A command-line task manager using Python and file handling.", "stack": "Python, File I/O", "level": "Beginner"},
            {"title": "Web Scraper", "desc": "Scrape job listings from a website and save to CSV.", "stack": "Python, BeautifulSoup, CSV", "level": "Intermediate"},
            {"title": "Expense Tracker", "desc": "Track daily expenses with charts using matplotlib.", "stack": "Python, Matplotlib, Pandas", "level": "Intermediate"},
            {"title": "Telegram Bot", "desc": "Build a bot that answers career-related questions.", "stack": "Python, Telegram API", "level": "Advanced"},
        ],
        "task_ids": [1, 2, 3, 4, 5],
    },
    {
        "name": "Backend Developer",
        "icon": "server",
        "blurb": "Design APIs, model data and keep services fast and reliable.",
        "skills": [
            "Python", "SQL", "MySQL", "Flask", "Django", "Git", "REST APIs", "Authentication",
        ],
        "roadmap": [
            {"title": "Learn Python", "detail": "Comfortable with the language before the stack."},
            {"title": "SQL & MySQL", "detail": "Joins, indexes, normalisation and query plans."},
            {"title": "Learn Flask", "detail": "Micro-framework routing, blueprints and error handling."},
            {"title": "Learn Django", "detail": "ORM, admin, auth and the request/response cycle."},
            {"title": "Git & GitHub", "detail": "Branching, pull requests and CI basics."},
            {"title": "Build Backend Projects", "detail": "A REST API with auth, tests and a deploy."},
        ],
        "projects": [
            {"title": "REST API", "desc": "Build a CRUD REST API for a blog with authentication.", "stack": "Python, Flask, MySQL", "level": "Beginner"},
            {"title": "Login System", "desc": "User registration, login, and JWT-based auth system.", "stack": "Django, SQLite, JWT", "level": "Intermediate"},
            {"title": "E-commerce Backend", "desc": "Product, cart, and order management API.", "stack": "Django REST Framework, PostgreSQL", "level": "Advanced"},
            {"title": "File Upload API", "desc": "API that accepts, stores, and serves uploaded files.", "stack": "Flask, AWS S3", "level": "Intermediate"},
        ],
        "task_ids": [6, 7, 8, 9],
    },
    {
        "name": "Full Stack Developer",
        "icon": "layers",
        "blurb": "Build the whole thing – interface, API and deployment.",
        "skills": [
            "HTML", "CSS", "JavaScript", "Python", "Django", "Git", "React", "SQL",
        ],
        "roadmap": [
            {"title": "HTML", "detail": "Semantic structure and accessible markup."},
            {"title": "CSS", "detail": "Flexbox, Grid and responsive breakpoints."},
            {"title": "JavaScript", "detail": "DOM, events, promises and fetch."},
            {"title": "Python", "detail": "The server side of the stack."},
            {"title": "Django", "detail": "Models, views, templates and the ORM."},
            {"title": "Full Stack Projects", "detail": "Ship an end-to-end app users can actually use."},
        ],
        "projects": [
            {"title": "Portfolio Website", "desc": "Your personal portfolio with projects and contact form.", "stack": "HTML, CSS, JavaScript", "level": "Beginner"},
            {"title": "Notes App", "desc": "Full stack notes app with login and CRUD.", "stack": "Django, HTML, CSS, JS", "level": "Intermediate"},
            {"title": "Job Board", "desc": "Post and search jobs with filters and user auth.", "stack": "React, Django, PostgreSQL", "level": "Advanced"},
            {"title": "Chat App", "desc": "Real-time messaging app using WebSockets.", "stack": "Django Channels, React, Redis", "level": "Advanced"},
        ],
        "task_ids": [10, 11, 12, 13],
    },
    {
        "name": "Data Analyst",
        "icon": "chart",
        "blurb": "Turn raw data into decisions people can act on.",
        "skills": [
            "Python", "Excel", "SQL", "Pandas", "NumPy", "Power BI", "Data Visualization", "Statistics",
        ],
        "roadmap": [
            {"title": "Python", "detail": "A working base for everything that follows."},
            {"title": "Excel", "detail": "Formulas, pivots and cleaning messy sheets."},
            {"title": "SQL", "detail": "SELECT, JOIN, GROUP BY and window functions."},
            {"title": "Pandas", "detail": "DataFrames, grouping, joins and cleaning."},
            {"title": "Power BI", "detail": "Interactive dashboards and DAX basics."},
            {"title": "Analytics Projects", "detail": "Two end-to-end analyses with written findings."},
        ],
        "projects": [
            {"title": "Sales Dashboard", "desc": "Analyze monthly sales data with charts and trends.", "stack": "Python, Pandas, Matplotlib", "level": "Beginner"},
            {"title": "COVID Data Analysis", "desc": "Explore and visualize global COVID statistics.", "stack": "Python, Pandas, Seaborn", "level": "Beginner"},
            {"title": "Movie Recommender", "desc": "Recommend movies based on genre and rating similarity.", "stack": "Python, Scikit-learn, Pandas", "level": "Intermediate"},
            {"title": "Power BI Report", "desc": "Interactive HR analytics dashboard in Power BI.", "stack": "Power BI, Excel, SQL", "level": "Intermediate"},
        ],
        "task_ids": [14, 15, 16, 17],
    },
]

CAREER_NAMES = [c["name"] for c in CAREERS]

_CAREERS_BY_NAME = {c["name"]: c for c in CAREERS}


def get_career(name):
    """Return a career definition by name, or ``None``."""
    if not name:
        return None
    return _CAREERS_BY_NAME.get(name)


def career_skills(name):
    """Skill list for a career (empty list when unknown)."""
    career = get_career(name)
    return list(career["skills"]) if career else []


def tasks_for_career(name):
    """Progress tasks belonging to a career, in id order."""
    return [t for t in PROGRESS_TASKS if t["goal"] == name]


def all_tasks():
    """Every progress task, in id order."""
    return list(PROGRESS_TASKS)


def serialize_career(career):
    """JSON-safe representation of a career, including its progress tasks."""
    return {
        "name": career["name"],
        "icon": career["icon"],
        "blurb": career["blurb"],
        "skills": list(career["skills"]),
        "roadmap": list(career["roadmap"]),
        "projects": list(career["projects"]),
        "tasks": tasks_for_career(career["name"]),
        "task_ids": list(career["task_ids"]),
    }


@careers_bp.route("/api/careers", methods=["GET"])
def list_careers():
    """Public catalogue of the four career pathways."""
    return jsonify(
        {
            "careers": [serialize_career(c) for c in CAREERS],
            "names": list(CAREER_NAMES),
            "tasks": all_tasks(),
        }
    ), 200
