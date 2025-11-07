from flask import Blueprint, request
from datetime import datetime, timezone
from mongoengine import ValidationError

from mongo import *
from mongo.engine import PersonalAccessToken
from mongo.user import ROLE_SCOPE_MAP
from .auth import *
from .utils import *
from .utils.pat import hash_pat_token, validate_scope_for_role

__all__ = ['profile_api']

profile_api = Blueprint('profile_api', __name__)


@profile_api.route('/', methods=['GET'])
@profile_api.route('/<username>', methods=['GET'])
@login_required
def view_profile(user, username=None):
    user = user if username is None else User(username)
    if not user:
        return HTTPError('Profile not exist.', 404)

    data = {
        'email': user.obj.email,
        'displayedName': user.obj.profile.displayed_name,
        'bio': user.obj.profile.bio
    }
    data.update(user.info)

    return HTTPResponse('Profile exist.', data=data)


@profile_api.route('/', methods=['POST'])
@login_required
@Request.json('bio', vars_dict={'displayed_name': 'displayedName'})
def edit_profile(user, displayed_name, bio):
    profile = user.obj.profile or {}

    if displayed_name is not None:
        profile[
            'displayed_name'] = displayed_name if displayed_name != "" else user.username
    if bio is not None:
        profile['bio'] = bio

    user.obj.update(profile=profile)

    cookies = {'jwt': user.cookie}
    return HTTPResponse('Uploaded.', cookies=cookies)


@profile_api.route('/config', methods=['PUT'])
@login_required
@Request.json('font_size', 'theme', 'indent_type', 'tab_size', 'language')
def edit_config(user, font_size, theme, indent_type, tab_size, language):
    try:
        config = {
            'font_size': font_size,
            'theme': theme,
            'indent_type': indent_type,
            'tab_size': tab_size,
            'language': language
        }
        user.obj.update(editor_config=config)
    except ValidationError as ve:
        return HTTPError('Update fail.', 400, data=ve.to_dict())
    user.reload()
    cookies = {'jwt': user.cookie}
    return HTTPResponse('Uploaded.', cookies=cookies)


from model.utils.pat import (add_pat_to_database, _clean_token)

import secrets
from uuid import uuid4


@profile_api.route("/api_token", methods=["GET"])
@login_required
def get_tokens(user):
    tokens = []

    if user.role == Role.ADMIN:
        pat_objects = PersonalAccessToken.objects()
    else:
        pat_objects = PersonalAccessToken.objects(owner=user.username)
    tokens = [_clean_token(pat) for pat in pat_objects]
    return HTTPResponse("OK", data={"Tokens": tokens})


@profile_api.route("/api_token/getscope", methods=["GET"])
@login_required
def get_scope(user):
    user_role_key = user.role.value if hasattr(user.role,
                                               'value') else user.role
    scopes = ROLE_SCOPE_MAP.get(user_role_key, [])
    return HTTPResponse("OK", data={"Scope": list(scopes)})


@profile_api.route("/api_token/create", methods=["POST"])
@login_required
@Request.json("Name", "Scope")
def create_token(user, Name, Scope):
    pat_id = uuid4().hex[:16]
    secret = secrets.token_urlsafe(32)
    presented_token = f"noj_pat_{secret}"
    hash_val = hash_pat_token(presented_token)

    data = request.get_json()
    Due_Time = data.get("Due_Time", None)

    due_time_obj = None
    if Due_Time and Due_Time is not None:
        try:
            due_time_obj = datetime.fromisoformat(
                Due_Time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            due_time_obj = None

    if due_time_obj:
        now = datetime.now(timezone.utc)
        if due_time_obj.tzinfo is None:
            due_time_obj = due_time_obj.replace(tzinfo=timezone.utc)
        if due_time_obj <= now:
            return HTTPError(
                "Due_Time must be in the future",
                400,
                data={
                    "Type": "ERR",
                    "Message": "Due_Time must be in the future"
                },
            )

    Scope_Set = list(set(Scope)) if Scope else []

    if not validate_scope_for_role(Scope_Set, user.role, ROLE_SCOPE_MAP):
        return HTTPError("Invalid Scope",
                         400,
                         data={
                             "Type": "ERR",
                             "Message": "Invalid Scope"
                         })

    try:
        pat = add_pat_to_database(
            pat_id=pat_id,
            name=Name,
            owner=user.username,
            hash_val=hash_val,
            scope=Scope_Set,
            due_time=due_time_obj,
        )

        return HTTPResponse(
            "Token Created",
            data={
                "Type": "OK",
                "Message": "Token Created",
                "Token": presented_token
            },
        )
    except Exception as e:
        return HTTPError(
            "Failed to create token",
            500,
            data={
                "Type": "ERR",
                "Message": f"Database error: {str(e)}"
            },
        )


@profile_api.route("/api_token/edit/<pat_id>", methods=["PATCH"])
@login_required
@Request.json("data")
def edit_token(user, pat_id, data):
    if not data:
        return HTTPError("No data provided",
                         400,
                         data={
                             "Type": "ERR",
                             "Message": "No data provided"
                         })

    try:
        pat = PersonalAccessToken.objects.get(pat_id=pat_id)
    except PersonalAccessToken.DoesNotExist:
        return HTTPError("Token not found",
                         404,
                         data={
                             "Type": "ERR",
                             "Message": "Token not found"
                         })

    if pat.owner != user.username:
        return HTTPError("Not token owner",
                         403,
                         data={
                             "Type": "ERR",
                             "Message": "Not token owner"
                         })

    update_data = {}
    if "Name" in data:
        update_data["name"] = data["Name"]
    if "Due_Time" in data:
        try:
            if data["Due_Time"]:
                update_data["due_time"] = datetime.fromisoformat(
                    data["Due_Time"].replace("Z", "+00:00"))
            else:
                update_data["due_time"] = None
        except (ValueError, AttributeError):
            return HTTPError(
                "Invalid Due_Time format",
                400,
                data={
                    "Type": "ERR",
                    "Message": "Invalid Due_Time format"
                },
            )
    if "Scope" in data:
        update_data["scope"] = list(data["Scope"])

    try:
        pat.update(**update_data)
        return HTTPResponse("Token updated",
                            data={
                                "Type": "OK",
                                "Message": "Token updated"
                            })
    except Exception as e:
        return HTTPError(
            "Failed to update token",
            500,
            data={
                "Type": "ERR",
                "Message": f"Database error: {str(e)}"
            },
        )


@profile_api.route("/api_token/deactivate/<pat_id>", methods=["PATCH"])
@login_required
def deactivate_token(user, pat_id):
    try:
        pat = PersonalAccessToken.objects.get(pat_id=pat_id)
    except PersonalAccessToken.DoesNotExist:
        return HTTPError("Token not found",
                         404,
                         data={
                             "Type": "ERR",
                             "Message": "Token not found"
                         })

    if pat.owner != user.username:
        return HTTPError("Not token owner",
                         403,
                         data={
                             "Type": "ERR",
                             "Message": "Not token owner"
                         })

    if pat.is_revoked:
        return HTTPError(
            "Token already revoked",
            400,
            data={
                "Type": "ERR",
                "Message": "Token already revoked"
            },
        )

    try:
        pat.update(
            is_revoked=True,
            revoked_by=user.username,
            revoked_time=datetime.now(timezone.utc),
        )
        return HTTPResponse("Token revoked",
                            data={
                                "Type": "OK",
                                "Message": "Token revoked"
                            })
    except Exception as e:
        return HTTPError(
            "Failed to revoke token",
            500,
            data={
                "Type": "ERR",
                "Message": f"Database error: {str(e)}"
            },
        )
