import json
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from models import QuizResult
from extensions import db

quiz_bp = Blueprint('quiz_bp', __name__)

@quiz_bp.route('/api/quiz/submit', methods=['POST'])
@login_required
def submit_score():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
        
    topic = data.get('topic', 'Career Compatibility Assessment')
    score = data.get('score')
    total_questions = data.get('total_questions')
    percentage = data.get('percentage')
    compatible_career = data.get('compatible_career')
    compatibility_scores = data.get('compatibility_scores')  # Dict of career scores
    
    if score is None or total_questions is None or percentage is None or compatible_career is None:
        return jsonify({'error': 'Missing required fields'}), 400
        
    # Serialize compatibility scores to JSON string
    scores_str = json.dumps(compatibility_scores) if compatibility_scores else '{}'
    
    # Create new quiz result entry
    result = QuizResult(
        user_id=current_user.id,
        topic=topic,
        score=int(score),
        total_questions=int(total_questions),
        percentage=int(percentage),
        compatible_career=compatible_career,
        compatibility_scores=scores_str
    )
    
    try:
        db.session.add(result)
        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Career assessment score saved successfully',
            'result': {
                'id': result.id,
                'topic': result.topic,
                'score': result.score,
                'total_questions': result.total_questions,
                'percentage': result.percentage,
                'compatible_career': result.compatible_career,
                'taken_at': result.taken_at.strftime('%Y-%m-%d %H:%M')
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Database error occurred'}), 500

@quiz_bp.route('/api/quiz/history', methods=['GET'])
@login_required
def get_history():
    results = QuizResult.query.filter_by(user_id=current_user.id).order_by(QuizResult.taken_at.desc()).all()
    history = []
    for r in results:
        scores_dict = {}
        if r.compatibility_scores:
            try:
                scores_dict = json.loads(r.compatibility_scores)
            except Exception:
                scores_dict = {}
                
        history.append({
            'id': r.id,
            'topic': r.topic,
            'score': r.score,
            'total_questions': r.total_questions,
            'percentage': r.percentage,
            'compatible_career': r.compatible_career,
            'compatibility_scores': scores_dict,
            'taken_at': r.taken_at.strftime('%b %d, %Y %I:%M %p')
        })
    return jsonify({'history': history}), 200
