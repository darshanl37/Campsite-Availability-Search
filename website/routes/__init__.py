from website.routes.auth_routes import auth_bp
from website.routes.subscription_routes import subscription_bp
from website.routes.payment_routes import payment_bp

def register_routes(app):
    """Register all blueprint routes with the app."""
    app.register_blueprint(auth_bp)
    app.register_blueprint(subscription_bp)
    app.register_blueprint(payment_bp) 