from flask import Blueprint, request, redirect, render_template, url_for, flash, session, jsonify
from ..services import auth_service
from ..models import db
from datetime import datetime

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        result = auth_service.login_user(email, password)
        
        if result['success']:
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            flash(result['error'], 'error')
    
    return render_template('auth/login.html')

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        
        result = auth_service.create_user(email, password, name)
        
        if result['success']:
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('auth.login'))
        else:
            flash(result['error'], 'error')
    
    return render_template('auth/signup.html')

@auth_bp.route('/logout')
def logout():
    auth_service.logout_user()
    return redirect(url_for('index'))

@auth_bp.route('/google')
def google_login():
    """Initiate Google OAuth flow."""
    # Get the URL for Google OAuth
    redirect_uri = url_for('auth.google_callback', _external=True)
    
    # Store the next URL in session if it's provided
    next_url = request.args.get('next')
    if next_url:
        session['oauth_next'] = next_url
    
    # Force HTTPS for OAuth
    redirect_uri = redirect_uri.replace('http://', 'https://')
    
    # Debug: Print the redirect URI
    print(f"DEBUG - Google login redirect URI: {redirect_uri}")
    
    auth_url = auth_service.get_google_auth_url(redirect_uri)
    
    if not auth_url:
        flash('Google authentication is not configured.', 'error')
        return redirect(url_for('auth.login'))
    
    return redirect(auth_url)

@auth_bp.route('/callback')
def google_callback():
    """Handle Google OAuth callback."""
    # Get authorization code from Google
    code = request.args.get('code')
    if not code:
        print("ERROR - Google callback: No authorization code received")
        flash('Authentication failed.', 'error')
        return redirect(url_for('auth.login'))
    
    # Process the callback
    redirect_uri = url_for('auth.google_callback', _external=True)
    
    # Force HTTPS for OAuth
    redirect_uri = redirect_uri.replace('http://', 'https://')
    
    print(f"DEBUG - Google callback processing with code: {code[:5]}... and redirect URI: {redirect_uri}")
    user = auth_service.process_google_callback(code, redirect_uri)
    
    if not user:
        print("ERROR - Google callback: User authentication failed")
        flash('Authentication failed.', 'error')
        return redirect(url_for('auth.login'))
    
    # Set user in session
    session['user_id'] = user.id
    print(f"SUCCESS - Google authentication for user: {user.email}")
    
    # Check if this is a first-time login (new user)
    is_new_user = user.last_login is None or (datetime.utcnow() - user.last_login).days > 30
    
    # Get the stored next URL from session or use default
    next_url = session.pop('oauth_next', None) or request.args.get('next') or url_for('auth.profile' if is_new_user else 'index')
    
    # If it's a new user, flash a welcome message
    if is_new_user:
        flash('Welcome! Your account has been created successfully.', 'success')
    
    return redirect(next_url)

@auth_bp.route('/profile', methods=['GET', 'POST'])
@auth_service.require_login
def profile():
    user = auth_service.get_current_user()
    
    if request.method == 'POST':
        # Update user profile
        name = request.form.get('name')
        
        if name:
            user.name = name
            
        # Update notification preferences
        try:
            preferences = {
                'email': 'email' in request.form,
                'sms': 'sms' in request.form,
                'whatsapp': 'whatsapp' in request.form
            }
            auth_service.update_notification_preferences(user.id, preferences)
            
            flash('Profile updated successfully.', 'success')
        except Exception as e:
            flash(f'Error updating profile: {str(e)}', 'error')
    
    return render_template('auth/profile.html', user=user)

@auth_bp.route('/verify', methods=['GET', 'POST'])
@auth_service.require_login
def verify():
    user = auth_service.get_current_user()
    verification_type = request.args.get('type', 'sms')
    
    if request.method == 'POST':
        # Verify code
        code = request.form.get('code')
        
        if code:
            if auth_service.verify_code(user.id, code, verification_type):
                flash(f'{verification_type.capitalize()} verified successfully.', 'success')
                return redirect(url_for('auth.profile'))
            else:
                flash('Invalid or expired verification code.', 'error')
    
    return render_template('auth/verify.html', user=user, verification_type=verification_type)

@auth_bp.route('/send_code', methods=['POST'])
@auth_service.require_login
def send_code():
    user = auth_service.get_current_user()
    verification_type = request.form.get('type', 'sms')
    
    # Update user phone/whatsapp if provided
    if verification_type == 'sms':
        phone = request.form.get('phone')
        if phone:
            user.phone = phone
    elif verification_type == 'whatsapp':
        whatsapp = request.form.get('whatsapp')
        if whatsapp:
            user.whatsapp = whatsapp
    
    # Save changes
    try:
        db.session.commit()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    
    # Generate and send verification code
    verification = auth_service.generate_verification_code(user.id, verification_type)
    
    if verification:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to send verification code.'}) 