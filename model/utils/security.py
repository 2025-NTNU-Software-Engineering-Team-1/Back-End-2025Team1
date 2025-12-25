from flask import request, abort, current_app, make_response
from .response import HTTPError


def setup_security(app):
    """
    Includes:
    - CORS (Cross-Origin Resource Sharing)
    - CSRF Protection (Origin/Referer check)
    - Security Headers (CSP, HSTS, etc.)
    - Global Error Handlers (404, 500)
    """

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get('Origin')
        if origin:
            # In production, you'd want to restrict this to trusted origins
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers[
                'Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, PATCH, OPTIONS'
            response.headers[
                'Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        return response

    @app.before_request
    def csrf_protection():
        if request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return

        # Allow requests with valid sandbox token (internal service calls)
        token = request.args.get('token')
        if token:
            from mongo import sandbox
            if sandbox.find_by_token(token) is not None:
                return  # Valid sandbox token, skip CSRF check

        # CSRF Protection via Origin/Referer Verification
        server_name = app.config.get('SERVER_NAME')
        target_origin = server_name.split(':')[0] if server_name else None

        origin = request.headers.get('Origin')
        referrer = request.headers.get('Referer')

        # Check Origin first (more secure)
        if origin:
            # origin usually comes as scheme://domain:port
            # We check if SERVER_NAME is contained or strictly matches appropriately
            # Simplified strict check:
            if server_name and server_name not in origin:
                app.logger.warning(
                    f'[CSRF Block] Origin mismatch: {origin} not in {server_name}'
                )
                abort(403)
            return

        # Fallback to Referer
        if referrer:
            if server_name and server_name not in referrer:
                app.logger.warning(
                    f'[CSRF Block] Referer mismatch: {referrer} not in {server_name}'
                )
                abort(403)
            return

        # If neither is present, and we have a server_name, we might block.
        # However, for local dev without SERVER_NAME, we allow.
        if server_name:
            app.logger.warning(
                '[CSRF Block] Missing Origin and Referer headers')
            abort(403)

    @app.after_request
    def security_headers(response):
        # Security Headers
        response.headers[
            'Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self' ws: wss:;"
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
