import os
import secrets
import typing
from typing import Any, Dict, Literal, Tuple, Union
import pytest

import mongomock
from flask.testing import FlaskClient
from mongo import *
from mongoengine import connect, disconnect


def random_string(k=None):
    '''
    return a random string 

    Args:
        k:
            the return string's byte length, if None,
            then use the `secrets` module's default 
            value. notice that the byte length will 
            not equal string length

    Returns:
        a random-generated string with length k
    '''
    return secrets.token_urlsafe(k)


@pytest.mark.usefixtures("setup_minio")
class BaseTester:
    MONGO_HOST = os.environ.get('MONGO_HOST', 'mongomock://localhost')
    DB = 'normal-oj'
    USER_CONFIG = 'tests/user.json'

    @classmethod
    def drop_db(cls):
        # Disconnect any existing connections first
        disconnect(alias='default')
        host = cls.MONGO_HOST
        if host.startswith('mongomock'):
            host = host.replace('mongomock', 'mongodb')
        conn = connect(
            cls.DB,
            host=host,
            mongo_client_class=mongomock.MongoClient,
        )
        conn.drop_database(cls.DB)

    @classmethod
    def setup_class(cls):
        cls.drop_db()
        with open(cls.USER_CONFIG) as f:
            import json
            config = json.load(f)
            users = {}
            tcls = cls
            while True:
                users.update(config.get(tcls.__name__, {}))
                if tcls.__name__ == 'BaseTester':
                    break
                tcls = tcls.__base__
            for name, role in users.items():
                cls.add_user(name, role)

    @classmethod
    def teardown_class(cls):
        cls.drop_db()

    @classmethod
    def add_user(cls, username, role=2):
        '''
        quickly add a new user (default role is student) and return it
        '''
        USER = {
            'username': username,
            'password': f'{username}_password',
            'email': f'i.am.{username}@noj.tw'
        }
        user = User.signup(**USER)
        user.update(
            active=True,
            role=role,
        )
        return user

    @staticmethod
    def request(
        client: FlaskClient,
        method: Literal['get', 'post', 'put', 'patch', 'delete'],
        url: str,
        **ks,
    ) -> Tuple['TestResponse', Union[Any, Dict[str, Any]], Union[Any, None]]:
        func = getattr(client, method)
        rv: 'TestResponse' = func(url, **ks)
        rv_json = rv.get_json()
        if isinstance(rv_json, dict):
            rv_data = rv_json.get('data')
        else:
            rv_data = None
        return rv, rv_json, rv_data


from .conftest import *
