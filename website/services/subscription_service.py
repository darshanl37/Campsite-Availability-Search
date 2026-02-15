import os
import subprocess
import logging
import hashlib
import json
from datetime import datetime, timedelta

from ..models import db, Subscription, Notification, User
from .notification_service import NotificationService
from ..scheduler import get_scheduler

logger = logging.getLogger(__name__)

# ---- module-level job function (must be importable / picklable) ----

def check_subscription(subscription_id, app_import_name):
    """Run a campsite check for one subscription.

    This function is the APScheduler job target.  It must be a module-level
    function (not a method) so APScheduler can serialise the reference.
    ``app_import_name`` is used to push an application context.
    """
    from flask import current_app
    from website.models import db, Subscription, User
    from website.services.notification_service import NotificationService

    # We may be running outside a request context, so push one.
    from flask import Flask
    # Import the app from the module that created it
    import importlib
    try:
        app_module = importlib.import_module(app_import_name)
        app = getattr(app_module, 'app', None)
    except Exception:
        app = None

    if app is None:
        logger.error("Could not obtain Flask app for subscription check")
        return

    with app.app_context():
        subscription = Subscription.query.filter_by(subscription_id=subscription_id).first()
        if not subscription or not subscription.active:
            return

        # Auto-expire past end_date
        if subscription.end_date < datetime.utcnow().date():
            subscription.active = False
            db.session.commit()
            logger.info(f"Auto-deactivated expired subscription {subscription_id}")
            sched = get_scheduler()
            if sched:
                try:
                    sched.remove_job(f"sub_{subscription_id}")
                except Exception:
                    pass
            return

        # Determine provider from the subscription's provider field or park_id prefix
        provider = getattr(subscription, 'provider', 'RecreationGov') or 'RecreationGov'
        park_id = subscription.park_id

        # Also detect from park_id prefix for backward compat
        if park_id.startswith('rc:'):
            provider = 'ReserveCalifornia'
            park_id = park_id[3:]
        elif park_id.startswith('rg:'):
            park_id = park_id[3:]

        if provider == 'ReserveCalifornia':
            # In-process search using our lightweight RC client
            from website.services.reserve_california import search_rc_availability
            try:
                rc_json = search_rc_availability(
                    [park_id],
                    subscription.start_date.strftime('%Y-%m-%d'),
                    subscription.end_date.strftime('%Y-%m-%d'),
                    subscription.nights,
                )
                output = json.dumps(rc_json) if rc_json else ''
            except Exception as e:
                logger.error(f"RC subscription {subscription_id} check failed: {e}")
                subscription.last_checked = datetime.utcnow()
                db.session.commit()
                return
        else:
            # Recreation.gov: existing subprocess path
            script_dir = os.environ.get('CAMPING_SCRIPT_DIR', '')
            python_path = os.environ.get('VENV_PYTHON', 'python')

            cmd = [
                python_path,
                os.path.join(script_dir, 'camping_wrapper.py'),
                '--start-date', subscription.start_date.strftime('%Y-%m-%d'),
                '--end-date', subscription.end_date.strftime('%Y-%m-%d'),
                '--parks', park_id,
                '--nights', str(subscription.nights),
                '--json-output',
            ]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    cwd=script_dir, timeout=120,
                )
                output = result.stdout.strip()
            except Exception as e:
                logger.error(f"Subscription {subscription_id} check failed: {e}")
                subscription.last_checked = datetime.utcnow()
                db.session.commit()
                return

        subscription.last_checked = datetime.utcnow()

        if not output:
            db.session.commit()
            return

        result_hash = hashlib.sha256(output.encode()).hexdigest()
        if result_hash == subscription.last_result_hash:
            db.session.commit()
            return

        # Results changed
        subscription.last_result_hash = result_hash

        # Rate-limit notifications to once per 24h
        send = True
        if subscription.last_notification:
            hours = (datetime.utcnow() - subscription.last_notification).total_seconds() / 3600
            if hours < 24:
                send = False

        if send:
            try:
                changes = json.loads(output) if output.startswith('{') else [output]
            except json.JSONDecodeError:
                changes = [output]

            _send_notification(subscription, changes)
            subscription.last_notification = datetime.utcnow()

        db.session.commit()


def _send_notification(subscription, changes):
    """Send notifications for a subscription change."""
    notification_service = NotificationService()
    user = User.query.get(subscription.user_id)

    if not user:
        return

    content = notification_service.format_campsite_availability_notification(subscription, changes)

    notification = Notification(
        subscription_id=subscription.id,
        message=json.dumps(changes) if isinstance(changes, (dict, list)) else str(changes),
    )
    db.session.add(notification)

    try:
        prefs = json.loads(user.notification_preferences)
    except Exception:
        prefs = {'email': True, 'sms': False, 'whatsapp': False}

    results = {}

    if prefs.get('email', True) and user.email:
        results['email'] = notification_service.send_email(
            user.email, content['subject'], content['html'],
        )
        notification.sent_email = results['email'].get('success', False)

    if prefs.get('sms', False) and user.phone and user.phone_verified:
        if user.can_use_sms():
            results['sms'] = notification_service.send_sms(user.phone, content['text'])
            notification.sent_sms = results['sms'].get('success', False)

    if prefs.get('whatsapp', False) and user.whatsapp and user.whatsapp_verified:
        if user.can_use_sms():
            results['whatsapp'] = notification_service.send_whatsapp(user.whatsapp, content['text'])
            notification.sent_whatsapp = results['whatsapp'].get('success', False)

    notification.delivery_status = json.dumps(results)
    db.session.commit()


# ---- SubscriptionService class ----

class SubscriptionService:
    def __init__(self):
        self.notification_service = NotificationService()

    # --- CRUD ---

    def create_subscription(self, user_id, park_id, campground_name, start_date,
                            end_date, nights, search_preference, check_frequency=60,
                            provider='RecreationGov'):
        subscription = Subscription(
            user_id=user_id,
            park_id=park_id,
            campground_name=campground_name,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
            nights=nights,
            search_preference=search_preference,
            check_frequency=check_frequency,
        )
        db.session.add(subscription)
        db.session.commit()

        self._schedule_job(subscription)
        return subscription

    def deactivate_subscription(self, subscription_id):
        subscription = Subscription.query.filter_by(subscription_id=subscription_id).first()
        if not subscription:
            return False
        subscription.active = False
        db.session.commit()
        self._remove_job(subscription)
        return True

    def reactivate_subscription(self, subscription_id):
        subscription = Subscription.query.filter_by(subscription_id=subscription_id).first()
        if not subscription:
            return False
        subscription.active = True
        db.session.commit()
        self._schedule_job(subscription)
        return True

    def update_subscription(self, subscription_id, **kwargs):
        subscription = Subscription.query.filter_by(subscription_id=subscription_id).first()
        if not subscription:
            return None

        for key, value in kwargs.items():
            if hasattr(subscription, key):
                setattr(subscription, key, value)
        db.session.commit()

        if subscription.active:
            self._remove_job(subscription)
            self._schedule_job(subscription)

        return subscription

    def validate_subscription(self, park_id, start_date, end_date, nights):
        errors = []
        if not park_id or not park_id.strip():
            errors.append("Park ID is required")
        today = datetime.now().date()
        if start_date < today:
            errors.append("Start date must be in the future")
        if end_date <= start_date:
            errors.append("End date must be after start date")
        if nights < 1:
            errors.append("Number of nights must be at least 1")
        date_range = (end_date - start_date).days
        if nights > date_range:
            errors.append(f"Requested {nights} nights exceeds date range of {date_range} days")
        return errors

    def get_user_subscriptions(self, user_id):
        return Subscription.query.filter_by(user_id=user_id).order_by(Subscription.created_at.desc()).all()

    # --- scheduler helpers ---

    def _schedule_job(self, subscription):
        sched = get_scheduler()
        if not sched:
            logger.warning("Scheduler not initialised — cannot schedule job")
            return

        job_id = f"sub_{subscription.subscription_id}"
        sched.add_job(
            check_subscription,
            'interval',
            minutes=subscription.check_frequency,
            id=job_id,
            args=[subscription.subscription_id, 'website.app'],
            replace_existing=True,
        )
        logger.info(f"Scheduled job {job_id} every {subscription.check_frequency} min")

    def _remove_job(self, subscription):
        sched = get_scheduler()
        if not sched:
            return
        job_id = f"sub_{subscription.subscription_id}"
        try:
            sched.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
        except Exception:
            pass

    # --- restore on startup ---

    @staticmethod
    def restore_active_watches():
        """Re-schedule jobs for all active subscriptions. Call on app startup."""
        sched = get_scheduler()
        if not sched:
            logger.warning("Scheduler not available — skipping watch restore")
            return

        active = Subscription.query.filter_by(active=True).all()
        restored = 0
        for sub in active:
            # Auto-expire
            if sub.end_date < datetime.utcnow().date():
                sub.active = False
                db.session.commit()
                continue

            job_id = f"sub_{sub.subscription_id}"
            sched.add_job(
                check_subscription,
                'interval',
                minutes=sub.check_frequency,
                id=job_id,
                args=[sub.subscription_id, 'website.app'],
                replace_existing=True,
            )
            restored += 1

        logger.info(f"Restored {restored} active watch(es)")

    # Legacy compat — old routes may call these
    def stop_monitoring_process(self, subscription):
        self._remove_job(subscription)

    def start_monitoring_process(self, subscription):
        self._schedule_job(subscription)
