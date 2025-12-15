from flask import Blueprint, request
from datetime import datetime, timezone
from mongoengine import ValidationError

from mongo import *
# from mongo.engine import PersonalAccessToken  <-- Removing this
from mongo.user import ROLE_SCOPE_MAP
from .auth import *
from .utils import *

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


# ======================== pat ========================
from mongo.pat import PAT


@profile_api.route("/api_token", methods=["GET"])
@login_required
def get_tokens(user):
    # Admin can view all tokens, regular users only their own
    if user.role == Role.ADMIN:
        pat_objects = PAT.objects()
    else:
        pat_objects = PAT.objects(owner=user.username)

    # Use to_dict() for clean output
    tokens = [PAT(pat).to_dict() for pat in pat_objects]
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
    # Request.json cannot handle Due_Time, so manually process it
    data = request.get_json() or {}
    Due_Time = data.get("Due_Time", None)

    # Convert Due_Time string to datetime if provided
    due_time_obj = None
    if Due_Time:
        try:
            due_time_obj = datetime.fromisoformat(
                Due_Time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return HTTPError(
                "Invalid Due_Time format",
                400,
                data={
                    "Type": "ERR",
                    "Message": "Invalid Due_Time format"
                },
            )

    # Ensure Due_Time is in the future if provided
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

    # Ensure Scope is a list of unique values
    Scope_Set = list(set(Scope)) if Scope else []

    # Validate scope usage against user role
    if not PAT.validate_scope_for_role(Scope_Set, user.role, ROLE_SCOPE_MAP):
        return HTTPError("Invalid Scope",
                         400,
                         data={
                             "Type": "ERR",
                             "Message": "Invalid Scope"
                         })

    try:
        # Use simple generation method
        presented_token, _ = PAT.generate(name=Name,
                                          owner=user.username,
                                          scope=Scope_Set,
                                          due_time=due_time_obj)

        return HTTPResponse(
            "Token Created",
            data={
                "Type": "OK",
                "Message": "Token Created",
                "Token": presented_token
            },
        )
    except ValueError as e:
        return HTTPError(str(e), 400, data={"Type": "ERR", "Message": str(e)})
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

    # Retrieve via mongo layer wrapper
    pat = PAT(pat_id)
    if not pat:
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

    # Update fields if provided
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
        if update_data:
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
    # Retrieve via mongo layer wrapper
    pat = PAT(pat_id)
    if not pat:
        return HTTPError("Token not found",
                         404,
                         data={
                             "Type": "ERR",
                             "Message": "Token not found"
                         })

    try:
        # Use the revoke method from mongo/pat.py which handles permissions (Admin/Owner)
        pat.revoke(user)
        return HTTPResponse("Token revoked",
                            data={
                                "Type": "OK",
                                "Message": "Token revoked"
                            })
    except ValueError as e:
        return HTTPError(
            str(e),
            400,
            data={
                "Type": "ERR",
                "Message": str(e)
            },
        )
    except PermissionError as e:
        return HTTPError(
            str(e),
            403,
            data={
                "Type": "ERR",
                "Message": str(e)
            },
        )
    except Exception as e:
        return HTTPError(
            "Failed to revoke token",
            500,
            data={
                "Type": "ERR",
                "Message": f"Database error: {str(e)}"
            },
        )


# ======================== pat ends ========================
