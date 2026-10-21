"""Overview / Insights blueprint.

`compute_overview()` is shared by the JSON API and the server-rendered pages so
both always show the same numbers.
"""

import json

from flask import Blueprint, jsonify
from flask_login import current_user, login_required

from careers import PROGRESS_TASKS
from extensions import db
from models import Profile, QuizResult, Resume, UserProgress

overview_bp = Blueprint("overview_bp", __name__)


def compute_overview(user):
    """Build the overview payload for a user.

    Returns a plain dict so it can be used both by `jsonify` and by Jinja.
    """
    profile = Profile.query.filter_by(user_id=user.id).first()
    completed_count = UserProgress.query.filter_by(user_id=user.id, completed=True).count()
    total_tasks = len(PROGRESS_TASKS)
    progress_pct = round((completed_count / total_tasks) * 100) if total_tasks else 0
    latest_quiz = (
        QuizResult.query.filter_by(user_id=user.id).order_by(QuizResult.taken_at.desc()).first()
    )

    compatibility_scores = {}
    if latest_quiz and latest_quiz.compatibility_scores:
        try:
            compatibility_scores = json.loads(latest_quiz.compatibility_scores)
        except Exception:
            compatibility_scores = {}

    has_resume = Resume.query.filter_by(user_id=user.id).count() > 0

    return {
        "career_goal": profile.career_goal if profile else "Not Set",
        "progress_pct": progress_pct,
        "tasks_done": f"{completed_count} / {total_tasks}",
        "quiz_score": f"{latest_quiz.percentage}%" if latest_quiz else "No quiz taken",
        "latest_quiz_date": latest_quiz.taken_at.strftime("%Y-%m-%d") if latest_quiz else None,
        "compatibility_scores": compatibility_scores,
        # Extra fields used by the dashboard – additive, so the existing JSON
        # contract above is unchanged.
        "completed_count": completed_count,
        "total_tasks": total_tasks,
        "has_profile": bool(profile),
        "profile_complete": _profile_completeness(profile),
        "has_quiz": bool(latest_quiz),
        "has_resume": has_resume,
    }


def _profile_completeness(profile):
    """Percentage of the profile form that has been filled in."""
    if not profile:
        return 0
    fields = [
        profile.phone,
        profile.college_name,
        profile.degree,
        profile.skills,
        profile.career_goal,
    ]
    filled = sum(1 for f in fields if f and str(f).strip())
    return round((filled / len(fields)) * 100)


@overview_bp.route("/api/overview", methods=["GET"])
@login_required
def get_overview():
    return jsonify(compute_overview(current_user)), 200
