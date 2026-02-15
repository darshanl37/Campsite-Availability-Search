from flask import Blueprint, request, redirect, render_template, url_for, flash, jsonify
from datetime import datetime
from ..services import auth_service, subscription_service
from ..models import db, Subscription

subscription_bp = Blueprint('subscription', __name__, url_prefix='/subscription')

@subscription_bp.route('/')
@auth_service.require_login
def index():
    """List all subscriptions for the current user."""
    user = auth_service.get_current_user()
    subscriptions = subscription_service.get_user_subscriptions(user.id)
    return render_template('subscription/index.html', subscriptions=subscriptions)

@subscription_bp.route('/create', methods=['POST'])
@auth_service.require_login
def create():
    """Create a new subscription."""
    user = auth_service.get_current_user()

    try:
        # Extract form data
        park_id = request.form.get('parkId')
        campground_name = request.form.get('campgroundName')
        start_date = datetime.strptime(request.form.get('startDate'), '%Y-%m-%d').date()
        end_date = datetime.strptime(request.form.get('endDate'), '%Y-%m-%d').date()
        nights = int(request.form.get('nights', 1))
        search_preference = request.form.get('searchPreference', 'all')
        provider = request.form.get('provider', 'RecreationGov')

        # Validate subscription parameters
        errors = subscription_service.validate_subscription(park_id, start_date, end_date, nights)
        if errors:
            return jsonify({
                'success': False,
                'errors': errors
            })

        # Create subscription
        subscription = subscription_service.create_subscription(
            user.id, park_id, campground_name, start_date, end_date, nights,
            search_preference, provider=provider,
        )
        
        return jsonify({
            'success': True,
            'subscription_id': subscription.subscription_id,
            'message': 'Subscription created successfully'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@subscription_bp.route('/<subscription_id>', methods=['GET'])
@auth_service.require_login
def view(subscription_id):
    """View a specific subscription."""
    user = auth_service.get_current_user()
    subscription = Subscription.query.filter_by(
        subscription_id=subscription_id,
        user_id=user.id
    ).first_or_404()
    
    return render_template('subscription/view.html', subscription=subscription)

@subscription_bp.route('/<subscription_id>/update', methods=['POST'])
@auth_service.require_login
def update(subscription_id):
    """Update a subscription."""
    user = auth_service.get_current_user()
    subscription = Subscription.query.filter_by(
        subscription_id=subscription_id,
        user_id=user.id
    ).first_or_404()
    
    try:
        # Extract form data
        updates = {}
        
        if 'active' in request.form:
            updates['active'] = request.form.get('active') == 'true'
        
        if 'checkFrequency' in request.form:
            updates['check_frequency'] = int(request.form.get('checkFrequency'))
        
        # Update subscription
        subscription = subscription_service.update_subscription(
            subscription_id, **updates
        )
        
        return jsonify({
            'success': True,
            'message': 'Subscription updated successfully'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@subscription_bp.route('/<subscription_id>/delete', methods=['POST'])
@auth_service.require_login
def delete(subscription_id):
    """Delete a subscription."""
    user = auth_service.get_current_user()
    subscription = Subscription.query.filter_by(
        subscription_id=subscription_id,
        user_id=user.id
    ).first_or_404()
    
    try:
        # Stop monitoring process
        subscription_service.stop_monitoring_process(subscription)
        
        # Delete subscription
        db.session.delete(subscription)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Subscription deleted successfully'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@subscription_bp.route('/notify', methods=['POST'])
def notify_from_search():
    """Create a subscription from search results."""
    # Check if user is logged in
    user = auth_service.get_current_user()
    if not user:
        return jsonify({
            'success': False,
            'redirect': url_for('auth.login', next=url_for('subscription.notify_from_search')),
            'message': 'Please login to enable notifications'
        })
    
    try:
        # Extract form data
        park_id = request.form.get('parkId')
        campground_name = request.form.get('campgroundName')
        start_date = datetime.strptime(request.form.get('startDate'), '%Y-%m-%d').date()
        end_date = datetime.strptime(request.form.get('endDate'), '%Y-%m-%d').date()
        nights = int(request.form.get('nights', 1))
        search_preference = request.form.get('searchPreference', 'all')
        provider = request.form.get('provider', 'RecreationGov')

        # Check if user can receive notifications
        if not user.can_receive_notifications():
            return jsonify({
                'success': False,
                'redirect': url_for('payment.index'),
                'message': 'You have used all your free notifications. Please upgrade to continue.'
            })

        # Validate subscription parameters
        errors = subscription_service.validate_subscription(park_id, start_date, end_date, nights)
        if errors:
            return jsonify({
                'success': False,
                'errors': errors
            })

        # Create subscription
        subscription = subscription_service.create_subscription(
            user.id, park_id, campground_name, start_date, end_date, nights,
            search_preference, provider=provider,
        )
        
        return jsonify({
            'success': True,
            'subscription_id': subscription.subscription_id,
            'message': 'You will be notified when campsite availability changes.'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }) 