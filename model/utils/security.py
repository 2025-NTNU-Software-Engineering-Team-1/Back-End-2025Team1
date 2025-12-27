from flask import request, abort, current_app, make_response
from .response import HTTPError
import re


def _is_origin_allowed(origin: str, allowed_patterns: list) -> bool:
    """
    Check if the origin matches any of the allowed patterns.
    Supports wildcards: * matches any subdomain segment.
    Examples:
      - "https://example.com" matches exactly
      - "https://*.example.com" matches any subdomain
      - "http://localhost:*" matches any port
    """
    for pattern in allowed_patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        # Convert wildcard pattern to regex
        # Escape regex special chars except *
        regex_pattern = re.escape(pattern).replace(r'\*', '[^./]*')
        if re.match(f'^{regex_pattern}$', origin):
            return True
    return False


def _origin_matches_server_name(origin: str, server_name: str) -> bool:
    if not origin or not server_name:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        netloc = parsed.netloc
    except Exception:
        return False
    if not netloc:
        return False
    origin_host = netloc.split(':')[0]
    server_host = server_name.split(':')[0]
    return origin_host == server_host


def setup_security(app):
    """
    Includes:
    - CORS (Cross-Origin Resource Sharing)
    - CSRF Protection (Origin/Referer check)
    - Security Headers (CSP, HSTS, etc.)
    - Global Error Handlers (404, 500)
    """
    # Import security settings from centralized config
    from config import CORS_ALLOWED_ORIGINS, CSRF_STRICT_MODE

    allowed_origins = CORS_ALLOWED_ORIGINS
    csrf_strict = CSRF_STRICT_MODE

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get('Origin')
        if origin and _is_origin_allowed(origin, allowed_origins):
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
        # Check query string first, then JSON body
        token = request.args.get('token')
        if not token:
            # Try to get token from JSON body (for PUT/POST requests)
            try:
                json_data = request.get_json(silent=True)
                if json_data and isinstance(json_data, dict):
                    token = json_data.get('token')
            except Exception:
                pass
        if token:
            from mongo import sandbox
            if sandbox.find_by_token(token) is not None:
                return  # Valid sandbox token, skip CSRF check

        # CSRF Protection via Origin/Referer Verification
        server_name = app.config.get('SERVER_NAME')

        origin = request.headers.get('Origin')
        referrer = request.headers.get('Referer')

        # Check Origin first (more secure)
        if origin:
            if app.config.get('TESTING') and _origin_matches_server_name(
                    origin, server_name):
                return
            # In strict mode or when SERVER_NAME is set, validate against allowed origins
            if csrf_strict or server_name:
                if not _is_origin_allowed(origin, allowed_origins):
                    app.logger.warning(
                        f'[CSRF Block] Origin not in allowed list: {origin}')
                    abort(403)
            return

        # Fallback to Referer
        if referrer:
            if csrf_strict or server_name:
                # Extract origin from referer
                from urllib.parse import urlparse
                parsed = urlparse(referrer)
                ref_origin = f"{parsed.scheme}://{parsed.netloc}"
                if app.config.get('TESTING') and _origin_matches_server_name(
                        ref_origin, server_name):
                    return
                if not _is_origin_allowed(ref_origin, allowed_origins):
                    app.logger.warning(
                        f'[CSRF Block] Referer origin not allowed: {ref_origin}'
                    )
                    abort(403)
            return

        # If neither is present, block in strict mode or when SERVER_NAME is set
        if csrf_strict or server_name:
            app.logger.warning(
                '[CSRF Block] Missing Origin and Referer headers')
            abort(403)

    @app.after_request
    def security_headers(response):
        content_type = response.content_type or ''
        # Determine if we should set CSP
        # Skip CSP for API blueprints (ending in _api) or if JSON is returned
        is_api_blueprint = request.blueprint and request.blueprint.endswith(
            '_api')
        should_set_csp = not is_api_blueprint and (
            not app.config.get('TESTING')
            and 'application/json' not in content_type)

        if is_api_blueprint:
            response.headers['Cache-Control'] = 'no-store'

        if should_set_csp:
            response.headers['Content-Security-Policy'] = \
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " \
                "connect-src 'self' ws: wss:; img-src 'self' data: blob:; font-src 'self' data:;"
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
