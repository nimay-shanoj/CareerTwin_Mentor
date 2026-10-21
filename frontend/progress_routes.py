from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from models import UserProgress
from extensions import db

# The task list lives in `careers.py` – the single source of truth. The ids
# below are referenced by rows already stored in `user_progress`, so they must
# never be renumbered.
from careers import PROGRESS_TASKS

progress_bp = Blueprint('progress_bp', __name__)

__all__ = ["progress_bp", "PROGRESS_TASKS"]

@progress_bp.route('/api/progress', methods=['GET'])
@login_required
def get_progress():
    completed = UserProgress.query.filter_by(user_id=current_user.id, completed=True).all()
    completed_ids = [item.task_id for item in completed]
    total = len(PROGRESS_TASKS)
    completed_count = len(completed_ids)
    progress_pct = round((completed_count / total) * 100) if total else 0

    tasks_response = []
    for task in PROGRESS_TASKS:
        tasks_response.append({
            'id': task['id'],
            'goal': task['goal'],
            'task': task['task'],
            'desc': task['desc'],
            'completed': task['id'] in completed_ids
        })

    return jsonify({
        'tasks': tasks_response,
        'completed_ids': completed_ids,
        'completed_count': completed_count,
        'total_tasks': total,
        'progress_pct': progress_pct
    }), 200

@progress_bp.route('/api/progress/toggle', methods=['POST'])
@login_required
def toggle_progress():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request payload'}), 400

    task_id = data.get('task_id')
    completed = data.get('completed')

    if task_id is None or completed is None:
        return jsonify({'error': 'task_id and completed are required'}), 400

    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'task_id must be an integer'}), 400

    if task_id not in {t['id'] for t in PROGRESS_TASKS}:
        return jsonify({'error': 'Unknown task_id'}), 400

    record = UserProgress.query.filter_by(user_id=current_user.id, task_id=task_id).first()
    if not record:
        record = UserProgress(user_id=current_user.id, task_id=task_id, completed=bool(completed))
        db.session.add(record)
    else:
        record.completed = bool(completed)

    try:
        db.session.commit()
        return jsonify({'success': True, 'task_id': task_id, 'completed': bool(completed)}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Could not update progress task'}), 500

@progress_bp.route('/api/progress/reset', methods=['POST'])
@login_required
def reset_progress():
    try:
        UserProgress.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Could not reset progress'}), 500
