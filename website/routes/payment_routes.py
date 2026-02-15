from flask import Blueprint, request, redirect, render_template, url_for, flash, jsonify
from ..services import auth_service, payment_service
from ..models import db, Payment

payment_bp = Blueprint('payment', __name__, url_prefix='/payment')


@payment_bp.route('/')
@auth_service.require_login
def index():
    """Payment / tier selection page."""
    user = auth_service.get_current_user()
    payments = payment_service.get_user_payments(user.id)
    return render_template('payment/index.html', user=user, payments=payments)


@payment_bp.route('/stripe/create-checkout', methods=['POST'])
@auth_service.require_login
def create_stripe_checkout():
    """Create a Stripe Checkout session for a subscription tier."""
    user = auth_service.get_current_user()

    data = request.get_json(silent=True) or {}
    tier = data.get('tier', 'basic')

    if tier not in ('basic', 'supporter'):
        return jsonify({'success': False, 'error': 'Invalid tier.'}), 400

    try:
        success_url = url_for('payment.success', provider='stripe', _external=True) + '?session_id={CHECKOUT_SESSION_ID}'
        cancel_url = url_for('payment.cancel', provider='stripe', _external=True)

        result = payment_service.create_stripe_checkout_session(
            user.id, tier, success_url, cancel_url,
        )

        if not result or not result.get('success'):
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to create checkout session.'),
            })

        return jsonify({
            'success': True,
            'checkout_url': result['checkout_url'],
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@payment_bp.route('/success')
@auth_service.require_login
def success():
    user = auth_service.get_current_user()
    provider = request.args.get('provider', 'stripe')
    return render_template('payment/success.html', user=user, provider=provider)


@payment_bp.route('/cancel')
@auth_service.require_login
def cancel():
    user = auth_service.get_current_user()
    provider = request.args.get('provider', 'stripe')
    return render_template('payment/cancel.html', user=user, provider=provider)


@payment_bp.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.data
    signature = request.headers.get('Stripe-Signature')

    result = payment_service.handle_stripe_webhook(payload, signature)

    if result:
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 400
