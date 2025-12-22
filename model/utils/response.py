from flask import jsonify, redirect, current_app

__all__ = ['HTTPResponse', 'HTTPRedirect', 'HTTPError']


class HTTPBaseResponese(tuple):

    def __new__(
        cls,
        resp,
        status_code=200,
        cookies={},
    ):
        for c in cookies:
            if cookies[c] == None:
                resp.delete_cookie(c)
            else:
                d = c.split('_httponly')

                # Default security settings
                secure_flag = False
                try:
                    if current_app.config.get(
                            'PREFERRED_URL_SCHEME') == 'https':
                        secure_flag = True
                except RuntimeError:
                    pass  # Request context might not be active

                resp.set_cookie(d[0],
                                cookies[c],
                                httponly=bool(d[1:]),
                                samesite='Lax',
                                secure=secure_flag)
        return super().__new__(tuple, (resp, status_code))


class HTTPResponse(HTTPBaseResponese):

    def __new__(
        cls,
        message='',
        status_code=200,
        status='ok',
        data=None,
        cookies={},
    ):
        resp = jsonify({
            'status': status,
            'message': message,
            'data': data,
        })
        return super().__new__(
            HTTPBaseResponese,
            resp,
            status_code,
            cookies,
        )


class HTTPRedirect(HTTPBaseResponese):

    def __new__(
        cls,
        location,
        status_code=302,
        cookies={},
    ):
        resp = redirect(location)
        return super().__new__(
            HTTPBaseResponese,
            resp,
            status_code,
            cookies,
        )


class HTTPError(HTTPResponse):

    def __new__(
        cls,
        message,
        status_code,
        data=None,
        logout=False,
    ):
        cookies = {'piann': None, 'jwt': None} if logout else {}
        return super().__new__(
            HTTPResponse,
            message,
            status_code,
            'err',
            data,
            cookies,
        )
