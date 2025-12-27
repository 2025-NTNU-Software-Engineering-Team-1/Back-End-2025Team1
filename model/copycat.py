from typing import Dict
from mongo import Problem
from flask import Blueprint, request, current_app
from mongo.copycat_service import (
    build_student_dict,
    collect_submission_paths,
    get_course,
    get_problem,
    has_grade_permission,
    mark_report_requested,
    require_moss_userid,
    update_problem_report,
)
from .utils import *
from .auth import *

import mosspy
import threading
import logging
import requests
import re

__all__ = ['copycat_api']

copycat_api = Blueprint('copycat_api', __name__)


def is_valid_url(url):
    import re
    regex = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$',
        re.IGNORECASE)
    return url is not None and regex.search(url)


def get_report_task(user, problem_id, student_dict: Dict, moss_userid=None):
    last_cc_submission, last_python_submission = collect_submission_paths(
        user,
        problem_id,
        student_dict,
    )

    if moss_userid is None:
        moss_userid = require_moss_userid()

    # get logger
    logger = logging.getLogger('guincorn.error')

    # Get problem object
    problem = get_problem(problem_id)
    if not problem:
        logger.info(f"[copycat] problem not found: {problem_id}")
        return

    cpp_report_url = ''
    python_report_url = ''
    # check for c or cpp code
    if problem.allowed_language != 4:
        m1 = mosspy.Moss(moss_userid, "cc")

        for user, code_path in last_cc_submission.items():
            logger.info(f'send {user} {code_path}')
            m1.addFile(code_path)

        response = m1.send()
        if is_valid_url(response):
            cpp_report_url = response
        else:
            logger.info(f"[copycat] {response}")
            cpp_report_url = ''

    # check for python code
    if problem.allowed_language >= 4:
        m2 = mosspy.Moss(moss_userid, "python")

        for user, code_path in last_python_submission.items():
            logger.info(f'send {user} {code_path}')
            m2.addFile(code_path)

        response = m2.send()
        if is_valid_url(response):
            python_report_url = response
        else:
            logger.info(f"[copycat] {response}")
            python_report_url = ''

    # download report from moss
    if cpp_report_url != '':
        mosspy.download_report(
            cpp_report_url,
            f"submissions_report/{problem_id}/cpp_report/",
            connections=8,
            log_level=10,
        )
    if python_report_url != '':
        mosspy.download_report(
            python_report_url,
            f"submissions_report/{problem_id}/python_report/",
            connections=8,
            log_level=10,
        )

    # insert report url into DB & update status
    update_problem_report(
        problem_id,
        cpp_report_url=cpp_report_url,
        python_report_url=python_report_url,
        moss_status=2,
    )


def get_report_by_url(url: str):
    try:
        response = requests.get(url)
        return response.text
    except (requests.exceptions.MissingSchema,
            requests.exceptions.InvalidSchema):
        return 'No report.'


@copycat_api.route('/', methods=['GET'])
@login_required
@Request.args('course', 'problem_id')
def get_report(user, course, problem_id):
    if not (problem_id and course):
        return HTTPError(
            'missing arguments! (In HTTP GET argument format)',
            400,
            data={
                'need': ['course', 'problemId'],
            },
        )
    # some privilege or exist check
    try:
        problem = Problem(int(problem_id))
    except ValueError:
        return HTTPError('problemId must be integer', 400)

    if not problem:
        return HTTPError('Problem not exist.', 404)
    course = get_course(course)
    if not course:
        return HTTPError('Course not found.', 404)
    if not has_grade_permission(course, user):
        return HTTPError('Forbidden.', 403)

    cpp_report_url = problem.cpp_report_url
    python_report_url = problem.python_report_url

    if problem.moss_status == 0:
        return HTTPError(
            "No report found. Please make a post request to copycat api to generate a report",
            404,
            data={},
        )
    elif problem.moss_status == 1:
        return HTTPResponse("Report generating...", data={})
    else:
        cpp_report = get_report_by_url(cpp_report_url)
        python_report = get_report_by_url(python_report_url)
        return HTTPResponse(
            "Success.",
            data={
                "cpp_report": cpp_report,
                "python_report": python_report
            },
        )


@copycat_api.route('/', methods=['POST'])
@login_required
@Request.json('course', 'problem_id', 'student_nicknames')
def detect(user, course, problem_id, student_nicknames):
    if not (problem_id and course and type(student_nicknames) is dict):
        return HTTPError(
            'missing arguments! (In Json format)',
            400,
            data={
                'need': ['course', 'problemId', 'studentNicknames'],
            },
        )

    course = get_course(course)
    problem = get_problem(problem_id)

    # Check if student is in course
    # some privilege or exist check
    if not problem:
        return HTTPError('Problem not exist.', 404)
    if not course:
        return HTTPError('Course not found.', 404)
    if not has_grade_permission(course, user):
        return HTTPError('Forbidden.', 403)

    student_dict, err = build_student_dict(student_nicknames)
    if err is not None:
        return HTTPResponse(err, 404)

    if not current_app.config['TESTING']:
        try:
            moss_userid = require_moss_userid()
        except ValueError as exc:
            return HTTPError(str(exc), 500)
    else:
        moss_userid = None

    mark_report_requested(problem_id)
    if not current_app.config['TESTING']:
        threading.Thread(
            target=get_report_task,
            args=(
                user,
                problem_id,
                student_dict,
                moss_userid,
            ),
        ).start()

    # return Success
    return HTTPResponse('Success.')
