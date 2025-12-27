import os
from typing import Dict, Optional, Tuple

from mongo import Course, Problem, Submission, User


def get_moss_userid() -> Optional[int]:
    raw = os.getenv('MOSS_USERID')
    if raw is None or raw.strip() == '':
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def require_moss_userid() -> int:
    userid = get_moss_userid()
    if userid is None:
        raise ValueError('MOSS_USERID is not set or invalid.')
    return userid


def collect_submission_paths(user, problem_id,
                             student_dict: Dict) -> Tuple[Dict, Dict]:
    submissions = Submission.filter(
        user=user,
        offset=0,
        count=-1,
        status=0,
        problem=problem_id,
    )

    last_cc_submission = {}
    last_python_submission = {}
    for submission in submissions:
        s = Submission(submission.id)
        if s.user.username in student_dict:
            if s.language in [0, 1
                              ] and s.user.username not in last_cc_submission:
                last_cc_submission[
                    submission.user.username] = s.main_code_path()
            elif s.language in [
                    2
            ] and s.user.username not in last_python_submission:
                last_python_submission[
                    submission.user.username] = s.main_code_path()
    return last_cc_submission, last_python_submission


def get_problem(problem_id):
    return Problem(problem_id)


def get_course(course_id):
    return Course(course_id)


def has_grade_permission(course, user) -> bool:
    if not course:
        return False
    return course.permission(user, Course.Permission.GRADE)


def build_student_dict(
        student_nicknames: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    student_dict = {}
    for student, nickname in student_nicknames.items():
        if not User(student):
            return None, f'User: {student} not found.'
        student_dict[student] = nickname
    if not student_dict:
        return None, 'Empty student list.'
    return student_dict, None


def mark_report_requested(problem_id) -> bool:
    problem = Problem(problem_id)
    if not problem:
        return False
    problem.update(
        cpp_report_url="",
        python_report_url="",
        moss_status=1,
    )
    return True


def update_problem_report(
    problem_id,
    *,
    cpp_report_url: str,
    python_report_url: str,
    moss_status: int,
) -> bool:
    problem = Problem(problem_id)
    if not problem:
        return False
    problem.obj.update(
        cpp_report_url=cpp_report_url,
        python_report_url=python_report_url,
        moss_status=moss_status,
    )
    return True
