from flask import request, abort, current_app
from .response import HTTPError


def setup_security(app):
    """
    Setup security related configurations, headers, and error handlers.
    Includes:
    - CSRF Protection (Origin/Referer check)
    - Security Headers (CSP, HSTS, etc.)
    - Global Error Handlers (404, 500)
    """

    @app.before_request
    def csrf_protection():
        if request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return

        # CSRF Protection via Origin/Referer Verification
        server_name = app.config.get('SERVER_NAME')
        if not server_name:
            # Should not happen if setup_smtp logic is strict, but for safety
            return

        target_origin = server_name.split(':')[0]  # simple domain check

        origin = request.headers.get('Origin')
        referrer = request.headers.get('Referer')

        # Check Origin first (more secure)
        if origin:
            # origin usually comes as scheme://domain:port
            # We check if SERVER_NAME is contained or strictly matches appropriately
            # Simplified strict check:
            if server_name not in origin:
                app.logger.warning(
                    f'[CSRF Block] Origin mismatch: {origin} not in {server_name}'
                )
                abort(403)
            return

        # Fallback to Referer
        if referrer:
            if server_name not in referrer:
                app.logger.warning(
                    f'[CSRF Block] Referer mismatch: {referrer} not in {server_name}'
                )
                abort(403)
            return

        # If neither is present, for API security we might want to block or allow
        # Assuming modern browsers send at least one for CORS/Protect modes
        # For now, let's be safe and block if both are missing for state-changing requests
        app.logger.warning('[CSRF Block] Missing Origin and Referer headers')
        abort(403)

    @app.after_request
    def security_headers(response):
        # Security Headers
        response.headers['Content-Security-Policy'] = "default-src 'self';"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # HSTS: 1 year, include subdomains
        response.headers[
            'Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        # Remove Server header to prevent information disclosure
        response.headers.pop('Server', None)
        return response

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.error(f'Server Error: {e}')
        return HTTPError('Internal Server Error', 500)

    @app.errorhandler(404)
    def not_found(e):
        return HTTPError('Not Found', 404)
