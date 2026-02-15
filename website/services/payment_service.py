import os
import logging
import json
import stripe
from datetime import datetime
from flask import url_for

from ..models import db, Payment, User

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self):
        self.stripe_api_key = os.environ.get('STRIPE_SECRET_KEY')
        self.stripe_webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
        self.basic_price_id = os.environ.get('STRIPE_BASIC_PRICE_ID')
        self.supporter_price_id = os.environ.get('STRIPE_SUPPORTER_PRICE_ID')

        if self.stripe_api_key:
            stripe.api_key = self.stripe_api_key

    # ---- helpers ----

    def _get_or_create_customer(self, user):
        """Return an existing Stripe customer ID or create one."""
        if user.stripe_customer_id:
            return user.stripe_customer_id

        customer = stripe.Customer.create(email=user.email, name=user.name)
        user.stripe_customer_id = customer.id
        db.session.commit()
        return customer.id

    def _price_for_tier(self, tier):
        if tier == 'basic':
            return self.basic_price_id
        if tier == 'supporter':
            return self.supporter_price_id
        return None

    # ---- checkout ----

    def create_stripe_checkout_session(self, user_id, tier, success_url=None, cancel_url=None):
        """Create a Stripe Checkout session for a recurring subscription."""
        if not self.stripe_api_key:
            logger.error("Stripe API key not configured")
            return {'success': False, 'error': 'Stripe not configured'}

        user = User.query.get(user_id)
        if not user:
            return {'success': False, 'error': 'User not found'}

        price_id = self._price_for_tier(tier)
        if not price_id:
            return {'success': False, 'error': f'Unknown tier: {tier}'}

        try:
            customer_id = self._get_or_create_customer(user)

            if not success_url:
                success_url = url_for('payment.success', provider='stripe', _external=True) + '?session_id={CHECKOUT_SESSION_ID}'
            if not cancel_url:
                cancel_url = url_for('payment.cancel', provider='stripe', _external=True)

            session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=['card'],
                line_items=[{'price': price_id, 'quantity': 1}],
                mode='subscription',
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={'user_id': str(user_id), 'tier': tier},
            )

            amount = 1.00 if tier == 'basic' else 5.00
            payment = Payment(
                user_id=user_id,
                payment_id=session.id,
                amount=amount,
                currency='USD',
                provider='stripe',
                status='pending',
                payment_metadata=json.dumps({
                    'checkout_session_id': session.id,
                    'tier': tier,
                }),
            )
            db.session.add(payment)
            db.session.commit()

            return {
                'success': True,
                'checkout_url': session.url,
                'session_id': session.id,
            }

        except Exception as e:
            logger.error(f"Stripe checkout error: {e}")
            return {'success': False, 'error': str(e)}

    # ---- webhooks ----

    def handle_stripe_webhook(self, payload, signature):
        if not self.stripe_api_key or not self.stripe_webhook_secret:
            logger.error("Stripe not configured for webhooks")
            return False

        try:
            event = stripe.Webhook.construct_event(payload, signature, self.stripe_webhook_secret)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            return False

        event_type = event['type']
        obj = event['data']['object']

        if event_type == 'checkout.session.completed':
            self._handle_checkout_completed(obj)
        elif event_type == 'customer.subscription.deleted':
            self._handle_subscription_deleted(obj)
        elif event_type == 'invoice.payment_failed':
            self._handle_payment_failed(obj)

        return True

    def _handle_checkout_completed(self, session):
        payment = Payment.query.filter_by(payment_id=session['id']).first()
        if payment:
            payment.status = 'completed'

        user_id = session.get('metadata', {}).get('user_id')
        tier = session.get('metadata', {}).get('tier', 'basic')
        if user_id:
            user = User.query.get(int(user_id))
            if user:
                user.subscription_tier = tier
                user.subscription_expires = None  # managed by Stripe

        db.session.commit()
        logger.info(f"Checkout completed for user {user_id}, tier={tier}")

    def _handle_subscription_deleted(self, subscription_obj):
        customer_id = subscription_obj.get('customer')
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.subscription_tier = 'free'
            user.subscription_expires = datetime.utcnow()
            db.session.commit()
            logger.info(f"Subscription cancelled for user {user.id}")

    def _handle_payment_failed(self, invoice):
        customer_id = invoice.get('customer')
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            logger.warning(f"Payment failed for user {user.id}")

    # ---- queries ----

    def get_user_payments(self, user_id):
        return Payment.query.filter_by(user_id=user_id).order_by(Payment.created_at.desc()).all()
