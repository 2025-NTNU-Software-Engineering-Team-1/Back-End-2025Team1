import time
import json
from functools import wraps
from flask import request, current_app
from mongo import engine
from mongo.utils import doc_required
from .response import *

__all__ = (
    'Request',
    'get_ip',
)

type_map = {
    'int': int,
    'list': list,
    'str': str,
    'dict': dict,
    'bool': bool,
    'None': type(None)
}


# Refactored: The old one had terrible readability
class _Request(type):

    def __getattr__(self, content_type):

        def get(*keys, vars_dict={}):

            def data_func(func):

                @wraps(func)
                def wrapper(*args, **kwargs):
                    # 1. Acquiring the [Request] data
                    if content_type == 'json':
                        request_data = request.get_json(silent=True)
                        if request_data is None:
                            request_data = {}
                    else:
                        request_data = getattr(request, content_type)

                    if request_data is None:
                        return HTTPError(
                            f'Unaccepted Content-Type {content_type}', 415)

                    parsed_kwargs = {}

                    for key_spec in keys:
                        # 2. Parsing parameter specifications
                        if ':' in key_spec:
                            param_name, type_str = key_spec.split(':', 1)
                            param_name = param_name.strip()
                            target_type = type_map.get(type_str.strip())
                        else:
                            param_name = key_spec.strip()
                            target_type = None

                        # 3. Smart key lookup (Snake/Camel/Title)
                        #    Determine key name candidates
                        parts = [p for p in param_name.split('_') if p]
                        if not parts:
                            camel_key = param_name
                        else:
                            camel_key = parts[0] + ''.join(p.capitalize()
                                                           for p in parts[1:])

                        possible_keys = [
                            camel_key,  # languageType
                            param_name,  # language_type
                            param_name.title(),  # Language_Type
                            param_name.upper()  # LANGUAGE_TYPE
                        ]

                        value = None
                        found_key = None

                        for k in possible_keys:
                            if k in request_data:
                                value = request_data[k]
                                found_key = k
                                break

                        # 4. Checking for missing values
                        #    Required fields missing should directly report error
                        if value is None and target_type is not None:
                            if param_name not in vars_dict:
                                current_app.logger.error(
                                    f"[Request Parsing] Missing required field '{param_name}'. "
                                    f"Tried keys: {possible_keys}. "
                                    f"Received: {request_data}. Caller: {func.__name__}"
                                )
                                return HTTPError(
                                    'Requested Value With Wrong Type', 400)

                        # 5. Type conversion and validation
                        if target_type is not None and value is not None:
                            if not isinstance(value, target_type):
                                try:
                                    # Special handling for bool
                                    if target_type is bool:
                                        if isinstance(value, str):
                                            lower_val = value.lower()
                                            if lower_val == 'true':
                                                value = True
                                            elif lower_val == 'false':
                                                value = False
                                            else:
                                                raise ValueError(
                                                    f"Invalid boolean string: {value}"
                                                )
                                        else:
                                            # Strict bool check: reject int or other types
                                            raise ValueError(
                                                f"Strict bool check: cannot cast {type(value)} to bool"
                                            )
                                    else:
                                        # Other types attempt automatic conversion
                                        value = target_type(value)

                                except (ValueError, TypeError) as e:
                                    current_app.logger.error(
                                        f"[Request Parsing] Type mismatch for field '{found_key}'. "
                                        f"Expected {target_type.__name__}, got {type(value).__name__} ('{value}') "
                                        f"and failed to cast. Error: {e}")
                                    current_app.logger.error(
                                        f"[Request Parsing] Caller = {func.__name__}"
                                    )
                                    return HTTPError(
                                        'Requested Value With Wrong Type', 400)

                        parsed_kwargs[param_name] = value

                    # 6. Processing vars_dict
                    for v in vars_dict:
                        parsed_kwargs[v] = request_data.get(vars_dict[v])

                    kwargs.update(parsed_kwargs)
                    return func(*args, **kwargs)

                return wrapper

            return data_func

        return get


class Request(metaclass=_Request):

    @staticmethod
    def doc(src, des, cls=None, src_none_allowed=False):
        '''
        a warpper to `doc_required` for flask route
        '''

        def deco(func):

            @doc_required(src, des, cls, src_none_allowed)
            def inner_wrapper(*args, **ks):
                return func(*args, **ks)

            @wraps(func)
            def real_wrapper(*args, **ks):
                try:
                    return inner_wrapper(*args, **ks)
                # if document not exists in db
                except engine.DoesNotExist as e:
                    return HTTPError(str(e), 404)
                # if args missing
                except TypeError as e:
                    return HTTPError(str(e), 500)
                except engine.ValidationError as e:
                    current_app.logger.info(
                        f'Validation error [err={e.to_dict()}]')
                    return HTTPError('Invalid parameter', 400)

            return real_wrapper

        return deco


def get_ip() -> str:
    ip = request.headers.get('X-Forwarded-For', '').split(',')[-1].strip()
    return ip
