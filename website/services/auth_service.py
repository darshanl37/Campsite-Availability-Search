import os
import logging
import json
import random
import string
from datetime import datetime, timedelta
import requests
from flask import session, redirect, url_for, request
from oauthlib.oauth2 import WebApplicationClient

# Only allow insecure OAuth transport in debug mode
if os.environ.get('FLASK_DEBUG', '0') == '1':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from ..models import db, User, VerificationCode
from .notification_service import NotificationService

# Setup logging
logger = logging.getLogger(__name__)

class AuthService:
    def __init__(self):
        # Google OAuth configuration
        self.google_client_id = os.environ.get('GOOGLE_CLIENT_ID')
        self.google_client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
        self.google_discovery_url = "https://accounts.google.com/.well-known/openid-configuration"
        
        # Initialize notification service
        self.notification_service = NotificationService()
        
        # Initialize OAuth client if credentials are available
        self.google_client = None
        if self.google_client_id:
            self.google_client = WebApplicationClient(self.google_client_id)
    
    def get_google_provider_cfg(self):
        """Get Google's OAuth 2.0 endpoints."""
        try:
            return requests.get(self.google_discovery_url).json()
        except Exception as e:
            logger.error(f"Error getting Google provider config: {str(e)}")
            return None
    
    def get_google_auth_url(self, redirect_uri=None):
        """Get the Google authentication URL."""
        if not self.google_client or not self.google_client_id:
            logger.error("Google OAuth client not configured")
            return None
        
        # Get Google provider configuration
        google_provider_cfg = self.get_google_provider_cfg()
        if not google_provider_cfg:
            return None
        
        # Get the authorization endpoint
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]
        
        # Use default redirect URI if not provided
        if not redirect_uri:
            # This should be set to your actual domain in production
            server_name = request.host_url.rstrip('/')
            redirect_uri = f"{server_name}/auth/callback"
            # Force HTTPS for OAuth
            redirect_uri = redirect_uri.replace('http://', 'https://')
        
        # Generate the authorization URL
        return self.google_client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=redirect_uri,
            scope=["openid", "email", "profile"],
        )
    
    def process_google_callback(self, code, redirect_uri=None):
        """Process Google OAuth callback and create/update user."""
        if not self.google_client or not self.google_client_id:
            logger.error("Google OAuth client not configured (missing client ID)")
            return None
        
        # Get Google provider configuration
        google_provider_cfg = self.get_google_provider_cfg()
        if not google_provider_cfg:
            logger.error("Failed to get Google provider configuration")
            return None
        
        # Use default redirect URI if not provided
        if not redirect_uri:
            # This should be set to your actual domain in production
            server_name = request.host_url.rstrip('/')
            redirect_uri = f"{server_name}/auth/callback"
            # Force HTTPS for OAuth
            redirect_uri = redirect_uri.replace('http://', 'https://')
        
        # Get token endpoint
        token_endpoint = google_provider_cfg["token_endpoint"]
        
        try:
            logger.debug(f"Preparing token request for URI: {redirect_uri}")
            # Prepare and send token request
            token_url, headers, body = self.google_client.prepare_token_request(
                token_endpoint,
                authorization_response=request.url,
                redirect_url=redirect_uri,
                code=code
            )
            logger.debug(f"Sending token request to: {token_url}")
            token_response = requests.post(
                token_url,
                headers=headers,
                data=body,
                auth=(self.google_client_id, self.google_client_secret),
            ).json()
            
            logger.debug(f"Token response received: {token_response.get('token_type', 'No token_type')}")
            if 'error' in token_response:
                logger.error(f"Token response error: {token_response.get('error')}, {token_response.get('error_description', '')}")
                return None
                
            # Parse token response
            self.google_client.parse_request_body_response(json.dumps(token_response))
            
            # Get user info endpoint
            userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
            uri, headers, body = self.google_client.add_token(userinfo_endpoint)
            logger.debug(f"Getting user info from: {uri}")
            userinfo_response = requests.get(uri, headers=headers, data=body).json()
            
            if 'error' in userinfo_response:
                logger.error(f"User info error: {userinfo_response.get('error')}")
                return None
                
            # Verify email is verified
            if not userinfo_response.get("email_verified"):
                logger.warning(f"Email not verified: {userinfo_response.get('email')}")
                return None
            
            # Get user info
            google_id = userinfo_response["sub"]
            email = userinfo_response["email"]
            name = userinfo_response.get("name", email.split("@")[0])
            profile_picture = userinfo_response.get("picture")
            language_preference = userinfo_response.get("locale")
            
            logger.debug(f"Retrieved user info for: {email}")
            logger.debug(f"Profile picture: {profile_picture}")
            logger.debug(f"Language: {language_preference}")
            
            # Check if user exists
            user = User.query.filter_by(google_id=google_id).first()
            if not user:
                # Check by email
                user = User.query.filter_by(email=email).first()
                if user:
                    # Update existing user with Google ID
                    user.google_id = google_id
                    user.name = name
                    user.profile_picture = profile_picture
                    user.language_preference = language_preference
                    logger.debug(f"Updated existing user with Google ID: {email}")
                else:
                    # Create new user
                    user = User(
                        google_id=google_id,
                        email=email,
                        name=name,
                        profile_picture=profile_picture,
                        language_preference=language_preference
                    )
                    db.session.add(user)
                    logger.debug(f"Created new user: {email}")
            else:
                # Update existing user's info
                user.name = name
                user.profile_picture = profile_picture
                user.language_preference = language_preference
            
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            return user
            
        except Exception as e:
            logger.error(f"Error processing Google callback: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    def create_user(self, email, password=None, name=None):
        """Create a new user with email/password."""
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return {
                'success': False,
                'error': 'Email already registered'
            }
        
        try:
            # Create user
            user = User(
                email=email,
                name=name or email.split('@')[0]
            )
            
            # Set password if provided
            if password:
                user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            return {
                'success': True,
                'user_id': user.id
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating user: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def login_user(self, email, password):
        """Login a user with email/password."""
        user = User.query.filter_by(email=email).first()
        
        if not user or not user.password_hash:
            return {
                'success': False,
                'error': 'Invalid email or password'
            }
        
        if not user.check_password(password):
            return {
                'success': False,
                'error': 'Invalid email or password'
            }
        
        # Update last login
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        # Store user ID in session
        session['user_id'] = user.id
        
        return {
            'success': True,
            'user_id': user.id
        }
    
    def logout_user(self):
        """Logout a user by clearing the session."""
        session.pop('user_id', None)
        return True
    
    def get_current_user(self):
        """Get the current logged-in user."""
        user_id = session.get('user_id')
        if not user_id:
            return None
        
        return User.query.get(user_id)
    
    def require_login(self, func):
        """Decorator to require login for a view."""
        def wrapper(*args, **kwargs):
            user = self.get_current_user()
            if not user:
                return redirect(url_for('auth.login', next=request.path))
            return func(*args, **kwargs)
        
        # Preserve function metadata for Flask routing
        wrapper.__name__ = func.__name__
        return wrapper
    
    def generate_verification_code(self, user_id, verification_type):
        """Generate a verification code for SMS or WhatsApp."""
        user = User.query.get(user_id)
        if not user:
            return None
        
        # Generate a random 6-digit code
        code = ''.join(random.choices(string.digits, k=6))
        
        # Create verification code record
        verification = VerificationCode(
            user_id=user_id,
            code=code,
            verification_type=verification_type,
            expires_at=datetime.utcnow() + timedelta(minutes=10)
        )
        
        db.session.add(verification)
        db.session.commit()
        
        # Send the code
        self.notification_service.send_verification_code(user, code, verification_type)
        
        return verification
    
    def verify_code(self, user_id, code, verification_type):
        """Verify a code for SMS or WhatsApp."""
        # Get the most recent unused code that hasn't expired
        verification = VerificationCode.query.filter_by(
            user_id=user_id,
            code=code,
            verification_type=verification_type,
            used=False
        ).filter(
            VerificationCode.expires_at > datetime.utcnow()
        ).order_by(
            VerificationCode.created_at.desc()
        ).first()
        
        if not verification:
            return False
        
        # Mark code as used
        verification.used = True
        
        # Update user verification status
        user = User.query.get(user_id)
        if verification_type == 'sms':
            user.phone_verified = True
        elif verification_type == 'whatsapp':
            user.whatsapp_verified = True
        
        db.session.commit()
        
        return True
    
    def update_notification_preferences(self, user_id, preferences):
        """Update notification preferences for a user."""
        user = User.query.get(user_id)
        if not user:
            return False
        
        user.notification_preferences = json.dumps(preferences)
        db.session.commit()
        
        return True 