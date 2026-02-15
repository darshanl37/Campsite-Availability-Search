from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.parse

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    google_id = db.Column(db.String(120), unique=True, nullable=True)
    name = db.Column(db.String(120), nullable=True)
    profile_picture = db.Column(db.String(500), nullable=True)
    language_preference = db.Column(db.String(20), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    phone_verified = db.Column(db.Boolean, default=False)
    whatsapp = db.Column(db.String(20), nullable=True)
    whatsapp_verified = db.Column(db.Boolean, default=False)
    notification_preferences = db.Column(db.JSON, default=lambda: json.dumps({
        'email': True,
        'sms': False,
        'whatsapp': False
    }))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    # Legacy fields (kept for migration compat, unused)
    free_notifications_used = db.Column(db.Integer, default=0)
    paid_tier = db.Column(db.Boolean, default=False)

    # New tier model
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    subscription_tier = db.Column(db.String(20), default='free')  # 'free', 'basic', 'supporter'
    subscription_expires = db.Column(db.DateTime, nullable=True)

    subscriptions = db.relationship('Subscription', backref='user', lazy=True, cascade="all, delete-orphan")
    payments = db.relationship('Payment', backref='user', lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def _tier_active(self):
        """True if the paid subscription hasn't expired."""
        if self.subscription_tier == 'free':
            return False
        if self.subscription_expires and self.subscription_expires < datetime.utcnow():
            return False
        return True

    def can_use_sms(self):
        """SMS/WhatsApp requires basic or supporter tier."""
        return self.subscription_tier in ('basic', 'supporter') and self._tier_active()

    def max_watches(self):
        """Maximum active watches for this tier."""
        if self.subscription_tier == 'supporter' and self._tier_active():
            return 999  # effectively unlimited
        if self.subscription_tier == 'basic' and self._tier_active():
            return 10
        return 3  # free tier

    def can_receive_notifications(self):
        """All users can receive email notifications (unlimited)."""
        return True

    def increment_notification_count(self):
        """No-op — email is free for everyone now."""
        pass

class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    __table_args__ = (
        db.Index('idx_user_active', 'user_id', 'active'),
        db.Index('idx_active_checked', 'active', 'last_checked'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    subscription_id = db.Column(db.String(40), unique=True, default=lambda: str(uuid.uuid4()), index=True)
    park_id = db.Column(db.String(20), nullable=False)
    campground_name = db.Column(db.String(200), nullable=True)
    provider = db.Column(db.String(30), default='RecreationGov')  # 'RecreationGov' or 'ReserveCalifornia'
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    nights = db.Column(db.Integer, nullable=False)
    search_preference = db.Column(db.String(20), nullable=False)  # 'weekends', 'flexible', 'all'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True, index=True)
    last_checked = db.Column(db.DateTime, nullable=True, index=True)
    last_notification = db.Column(db.DateTime, nullable=True)
    last_result_hash = db.Column(db.String(64), nullable=True)
    check_frequency = db.Column(db.Integer, default=60)  # minutes
    process_pid = db.Column(db.Integer, nullable=True)  # Store PID of background process
    
    notifications = db.relationship('Notification', backref='subscription', lazy=True, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'<Subscription {self.subscription_id} for Park {self.park_id}>'

class Notification(db.Model):
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_email = db.Column(db.Boolean, default=False)
    sent_sms = db.Column(db.Boolean, default=False)
    sent_whatsapp = db.Column(db.Boolean, default=False)
    delivery_status = db.Column(db.JSON, nullable=True)  # Store delivery details
    
    def __repr__(self):
        return f'<Notification {self.id} for Subscription {self.subscription_id}>'

class Payment(db.Model):
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    payment_id = db.Column(db.String(100), nullable=True)  # External payment ID (Stripe, Venmo)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD')
    provider = db.Column(db.String(20), nullable=False)  # 'stripe', 'venmo'
    status = db.Column(db.String(20), nullable=False)  # 'pending', 'completed', 'failed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    payment_metadata = db.Column(db.JSON, nullable=True)  # Additional payment data
    
    def __repr__(self):
        return f'<Payment {self.id} of {self.amount} {self.currency} via {self.provider}>'

class VerificationCode(db.Model):
    __tablename__ = 'verification_codes'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    verification_type = db.Column(db.String(20), nullable=False)  # 'sms', 'whatsapp', 'email'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<VerificationCode for User {self.user_id}, Type: {self.verification_type}>'

class SearchHistory(db.Model):
    __tablename__ = 'search_history'
    __table_args__ = (
        db.Index('idx_user_created', 'user_id', 'created_at'),
        db.Index('idx_device_created', 'device_id', 'created_at'),
        db.Index('idx_park_id', 'park_id'),
        db.Index('idx_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)  # Nullable for anonymous users
    device_id = db.Column(db.String(64), nullable=True, index=True)  # Cookie-based ID for anonymous users
    
    # Search parameters
    park_id = db.Column(db.String(20), nullable=False, index=True)
    park_name = db.Column(db.String(200), nullable=True)
    provider = db.Column(db.String(30), default='RecreationGov')  # 'RecreationGov' or 'ReserveCalifornia'
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    nights = db.Column(db.Integer, nullable=False)
    search_preference = db.Column(db.String(20), nullable=False)  # 'weekends', 'flexible', 'all'
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    ip_address = db.Column(db.String(50), nullable=True)
    
    # Relationship
    user = db.relationship('User', backref=db.backref('search_history', lazy=True))
    
    def __repr__(self):
        return f'<SearchHistory for {self.park_name} ({self.park_id})>'
        
    def to_dict(self):
        """Convert to dictionary for easier templating."""
        # Create URL-safe versions of strings
        park_name_safe = urllib.parse.quote(self.park_name) if self.park_name else ''
        city_safe = urllib.parse.quote(self.city) if self.city else ''
        
        return {
            'id': self.id,
            'park_id': self.park_id,
            'park_name': self.park_name,
            'city': self.city,
            'state': self.state,
            'start_date': self.start_date.strftime('%Y-%m-%d') if self.start_date else '',
            'end_date': self.end_date.strftime('%Y-%m-%d') if self.end_date else '',
            'nights': self.nights,
            'search_preference': self.search_preference,
            'search_date': self.created_at.strftime('%b %d, %Y at %I:%M %p'),
            'search_url': f'/?parkId={self.park_id}&startDate={self.start_date.strftime("%Y-%m-%d") if self.start_date else ""}&endDate={self.end_date.strftime("%Y-%m-%d") if self.end_date else ""}&nights={self.nights}&searchPreference={self.search_preference}&campgroundName={park_name_safe}&city={city_safe}#results'
        } 