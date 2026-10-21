from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import Profile
from extensions import db

profile_bp = Blueprint('profile_bp', __name__)

@profile_bp.route('/profile', methods=['GET', 'POST'])
@profile_bp.route('/profile.html', methods=['GET', 'POST'])
@login_required
def profile():
    user_profile = current_user.profile
    
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        college_name = request.form.get('college_name')
        degree = request.form.get('degree')
        skills = request.form.get('skills')
        career_goal = request.form.get('career_goal')
        
        # Update user name
        current_user.name = name
        
        # Create or update profile record
        if not user_profile:
            user_profile = Profile(user_id=current_user.id)
            db.session.add(user_profile)
            
        user_profile.phone = phone
        user_profile.college_name = college_name
        user_profile.degree = degree
        user_profile.skills = skills
        user_profile.career_goal = career_goal
        
        try:
            db.session.commit()
            # Redirect to resume page as per original frontend workflow
            return redirect(url_for('resume_page'))
        except Exception as e:
            db.session.rollback()
            return render_template('profile.html', error_msg="Error saving profile details. Please try again.")
            
    return render_template('profile.html')
