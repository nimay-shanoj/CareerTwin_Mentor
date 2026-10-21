import os
import re
import uuid
import zipfile
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import Resume
from extensions import db

resume_bp = Blueprint('resume_bp', __name__)
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'rtf', 'odt'}

SKILL_KEYWORDS = [
    'python', 'django', 'flask', 'sql', 'mysql', 'postgresql', 'numpy', 'pandas',
    'javascript', 'html', 'css', 'react', 'aws', 'docker', 'git', 'api', 'rest',
    'java', 'c++', 'c#', '.net', 'kotlin', 'swift', 'ruby', 'php', 'scala',
    'tensorflow', 'pytorch', 'sklearn', 'excel', 'tableau', 'powerbi',
    'linux', 'windows', 'macos', 'unix', 'mongodb', 'redis', 'elasticsearch',
    'kubernetes', 'jenkins', 'bash', 'nodejs', 'npm', 'yarn'
]

SUGGESTION_KEYWORDS = {
    'python': 'Add more Python-related achievements or projects.',
    'django': 'Include Django or web framework experience.',
    'flask': 'Add Flask or lightweight web app experience.',
    'sql': 'Mention SQL queries or database design skills.',
    'mysql': 'Add MySQL database experience.',
    'react': 'Showcase React or frontend framework skills.',
    'aws': 'Include cloud or AWS skills.',
    'kubernetes': 'Add containerization and orchestration skills.',
    'mongodb': 'Mention NoSQL database experience.',
    'java': 'Add Java enterprise experience.',
}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_file(filepath):
    """Extract text from various file formats."""
    ext = filepath.rsplit('.', 1)[1].lower()

    try:
        if ext == 'pdf':
            try:
                from pypdf import PdfReader
                reader = PdfReader(filepath)
                text = '\n'.join((page.extract_text() or '') for page in reader.pages)
                if text.strip():
                    return text
            except Exception as e:
                print(f"PDF parse fallback: {e}")
        elif ext in ('docx', 'odt'):
            # Both are zip archives; the text lives in an XML member
            member = 'word/document.xml' if ext == 'docx' else 'content.xml'
            try:
                with zipfile.ZipFile(filepath) as z:
                    xml = z.read(member).decode('utf-8', errors='ignore')
                return re.sub(r'<[^>]+>', ' ', xml)
            except Exception as e:
                print(f"{ext} parse fallback: {e}")

        # txt, rtf, doc and any failed parse: best-effort raw decode
        with open(filepath, 'rb') as f:
            return f.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Error reading file: {e}")
        return ''


@resume_bp.route('/api/resume/analyze', methods=['POST'])
@login_required
def analyze_resume():
    if 'resume' not in request.files:
        return jsonify({'error': 'Resume file is required'}), 400

    file = request.files['resume']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': f'Unsupported file type. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

    filename = secure_filename(file.filename)
    upload_folder = current_app.config.get('UPLOAD_FOLDER')
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder, exist_ok=True)

    if not filename or '.' not in filename:
        return jsonify({'error': 'Invalid file name'}), 400

    # Unique stored name so different users/uploads never overwrite each other
    stored_name = f"{current_user.id}_{uuid.uuid4().hex[:8]}_{filename[-80:]}"
    filepath = os.path.join(upload_folder, stored_name)
    file.save(filepath)

    content = extract_text_from_file(filepath).lower()

    found_skills = []
    for skill in SKILL_KEYWORDS:
        # (?<!\w)/(?!\w) instead of \b so skills like 'c++', 'c#', '.net' can match
        if re.search(r'(?<!\w)' + re.escape(skill) + r'(?!\w)', content):
            found_skills.append(skill.title())

    found_skills = sorted(set(found_skills))
    score = min(100, max(0, 40 + len(found_skills) * 8))
    suggestions = []
    for key, suggestion in SUGGESTION_KEYWORDS.items():
        if key not in content and len(suggestions) < 5:
            suggestions.append(suggestion)

    resume_record = Resume(
        user_id=current_user.id,
        filename=filename,
        filepath=filepath,
        score=score,
        skills_found=', '.join(found_skills),
        suggestions=', '.join(suggestions)
    )

    try:
        db.session.add(resume_record)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Could not save resume analysis'}), 500

    return jsonify({
        'success': True,
        'filename': filename,
        'score': score,
        'skills_found': found_skills,
        'suggestions': suggestions,
        'uploaded_at': resume_record.uploaded_at.strftime('%Y-%m-%d %H:%M')
    }), 201


@resume_bp.route('/api/resume/latest', methods=['GET'])
@login_required
def get_latest_resume():
    resume = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.uploaded_at.desc()).first()
    if not resume:
        return jsonify({'resume': None}), 200

    return jsonify({
        'resume': {
            'filename': resume.filename,
            'score': resume.score,
            'skills_found': [s.strip() for s in resume.skills_found.split(',')] if resume.skills_found else [],
            'suggestions': [s.strip() for s in resume.suggestions.split(',')] if resume.suggestions else [],
            'uploaded_at': resume.uploaded_at.strftime('%Y-%m-%d %H:%M')
        }
    }), 200
