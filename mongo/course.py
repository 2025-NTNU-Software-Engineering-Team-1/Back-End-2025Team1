from . import engine
from .user import *
from .user import Role
from .utils import *
import re
import enum
import secrets
import string
from typing import Dict, List, Optional, Any
from .base import MongoBase
from datetime import datetime

__all__ = [
    'Course',
]


class Course(MongoBase, engine=engine.Course):

    class Permission(enum.IntFlag):
        VIEW = enum.auto()  # view course basic info
        SCORE = enum.auto()  # only can view self score
        MODIFY = enum.auto()  # manage course
        GRADE = enum.auto()  # grade students' score

    def check_privilege(self, user):
        return any((
            user.role == Role.ADMIN,
            bool(self.obj.teacher and user.pk == self.obj.teacher.pk),
            user.pk in [ta.pk for ta in self.obj.tas],
        ))

    def __new__(cls, course_name, *args, **kwargs):
        try:
            new = super().__new__(cls, course_name)
        except engine.ValidationError:
            try:
                pk = Course.engine.objects(course_name=course_name).get()
                new = super().__new__(cls, pk)
            except engine.DoesNotExist:
                new = super().__new__(cls, '0' * 24)
        return new

    def update_student_namelist(
        self,
        student_nicknames: Dict[str, str],
    ):
        from .homework import Homework
        if not all(User(name) for name in student_nicknames):
            raise engine.DoesNotExist(f'User not found')
        drop_user = set(self.student_nicknames) - set(student_nicknames)
        for user in drop_user:
            self.remove_user(User(user).obj)
        new_user = set(student_nicknames) - set(self.student_nicknames)
        for user in new_user:
            self.add_user(User(user).obj)
        self.student_nicknames = student_nicknames
        # TODO: use event to update homework data
        drop_user = [*map(User, drop_user)]
        new_user = [*map(User, new_user)]
        for homework in map(Homework, self.homeworks):
            homework.remove_student(drop_user)
            homework.add_student(new_user)
        self.save()

    def add_user(self, user: User):
        if not self:
            raise engine.DoesNotExist(f'Course [{self.course_name}]')
        user.update(add_to_set__courses=self.id)
        user.reload('courses')

    def remove_user(self, user: User):
        user.update(pull__courses=self.id)
        user.reload('courses')

    @classmethod
    def get_all(cls):
        return engine.Course.objects

    @classmethod
    def get_user_courses(cls, user):
        if user.role != Role.ADMIN:
            return user.courses
        else:
            return cls.get_all()

    def get_course_summary(self, problems: list):
        return {
            "course":
            self.course_name,
            "userCount":
            engine.User.objects(courses=self.id).count(),
            "homeworkCount":
            engine.Homework.objects(course_id=str(self.id)).count(),
            "submissionCount":
            engine.Submission.objects(problem__in=problems).count(),
        }

    def edit_course(self, user, new_course, teacher, color=None, emoji=None):
        if re.match(r'^[\w._\- ]+$', new_course) is None:
            raise ValueError

        if not self:
            raise engine.DoesNotExist('Course')

        if self.course_name == 'Public' and new_course != 'Public':
            raise PermissionError('Cannot rename Public course.')
        if self.course_name != 'Public' and new_course == 'Public':
            raise ValueError('Cannot rename course to Public.')

        if not perm(self, user):
            raise PermissionError
        te = User(teacher)
        if not te:
            raise engine.DoesNotExist('User')

        # HACK: not sure why the unique index is not work during the test
        if new_course != self.course_name and Course(new_course):
            raise engine.NotUniqueError('Course')

        self.course_name = new_course
        if te.obj != self.teacher:
            self.remove_user(self.teacher)
            self.add_user(te.obj)
        self.teacher = te.obj

        if color:
            if not re.match(r'^#[0-9a-fA-F]{6}$', color):
                raise ValueError('Invalid color format')
            self.color = color
        if emoji:
            if len(emoji) > 8:
                raise ValueError('Emoji too long')
            self.emoji = emoji

        self.save()
        return True

    def delete_course(self, user):
        if not self:
            # course not found
            raise engine.DoesNotExist('Course')
        if not perm(self, user):
            # user is not the TA or teacher in course
            raise PermissionError

        self.remove_user(self.teacher)
        self.delete()
        return True

    def get_scoreboard(
        self,
        problem_ids: List[int],
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> List[Dict]:
        scoreboard = []
        usernames = [User(u).id for u in self.student_nicknames.keys()]
        matching = {
            "user": {
                "$in": usernames
            },
            "problem": {
                "$in": problem_ids
            },
            "timestamp": {},
        }
        if start:
            matching['timestamp']['$gte'] = datetime.fromtimestamp(start)
        if end:
            matching['timestamp']['$lte'] = datetime.fromtimestamp(end)
        if not matching["timestamp"]:
            del matching["timestamp"]
        pipeline = [
            {
                "$match": matching
            },
            {
                "$group": {
                    "_id": {
                        "user": "$user",
                        "problem": "$problem",
                    },
                    "count": {
                        "$sum": 1
                    },
                    "max": {
                        "$max": "$score"
                    },
                    "min": {
                        "$min": "$score"
                    },
                    "avg": {
                        "$avg": "$score"
                    },
                }
            },
            {
                "$group": {
                    "_id": "$_id.user",
                    "scores": {
                        "$push": {
                            "pid": "$_id.problem",
                            "count": "$count",
                            "max": "$max",
                            "min": "$min",
                            "avg": "$avg",
                        },
                    },
                }
            },
        ]
        cursor = engine.Submission.objects().aggregate(pipeline)
        unrecorded_users = set(usernames)
        for item in cursor:
            sum_of_score = sum(s['max'] for s in item['scores'])
            scoreboard.append({
                'user': User(item['_id']).info,
                'sum': sum_of_score,
                'avg': sum_of_score / len(problem_ids),
                **{
                    f'{score["pid"]}': score
                    for score in item['scores']
                },
            })
            unrecorded_users.remove(item['_id'])
        for u in unrecorded_users:
            scoreboard.append({
                'user': User(u).info,
                'sum': 0,
                'avg': 0,
            })

        return scoreboard

    @staticmethod
    def generate_course_code(length: int = 8) -> str:
        """
        Generate a unique random course code.
        """
        chars = string.ascii_uppercase + string.digits
        while True:
            code = ''.join(secrets.choice(chars) for _ in range(length))
            # Check if code already exists
            if not engine.Course.objects(course_code=code).first():
                return code

    def add_auth_code(self, creator, max_usage=0):
        if not self:
            raise engine.DoesNotExist('Course')

        # Generate unique code
        while True:
            code = self.generate_course_code()
            if code == self.course_code:
                continue
            if any(ac.code == code for ac in self.auth_codes):
                continue
            break

        auth_code = engine.AuthorizationCode(
            code=code,
            max_usage=max_usage,
            creator=creator.obj if hasattr(creator, 'obj') else creator,
            is_active=True)

        self.update(push__auth_codes=auth_code)
        self.reload()
        return auth_code

    def remove_auth_code(self, code):
        if not self:
            raise engine.DoesNotExist('Course')

        # Use pull to remove from list
        # We can't really pull by code easily with EmbeddedDocument unless we match exact object
        # So filtering list and saving is easier
        filtered = [ac for ac in self.auth_codes if ac.code != code]
        if len(filtered) == len(self.auth_codes):
            return False  # Not found

        self.auth_codes = filtered
        self.save()
        return True

    @classmethod
    def get_by_code(cls, code: str) -> Optional['Course']:
        """
        Get a course by its course code.
        Returns None if not found.
        """
        try:
            course_doc = engine.Course.objects(
                engine.Q(course_code=code)
                | engine.Q(auth_codes__code=code)).first()
            if course_doc:
                return cls(course_doc)
            return None
        except Exception:
            return None

    def join_by_code(self, user, code: Optional[str] = None) -> bool:
        """
        Join course as a student using course code.
        Returns True if successful, raises exception otherwise.
        """
        if not self:
            raise engine.DoesNotExist('Course')

        user_wrapper = User(user) if isinstance(user, str) else user
        if not user_wrapper:
            raise engine.DoesNotExist('User')

        # Check if user is already in the course
        if self.id in [c.id for c in user_wrapper.obj.courses]:
            raise ValueError('User is already in this course')

        # Check if user is teacher or TA (they cannot join via code)
        if user_wrapper.obj == self.teacher:
            raise PermissionError(
                'Teacher cannot join their own course via code')
        if user_wrapper.obj in self.tas:
            raise PermissionError('TA cannot join via code')

        # Handle Authorization Code Logic
        if code:
            # Check if it is an auth code
            auth_code_obj = next(
                (ac for ac in self.auth_codes if ac.code == code), None)
            if auth_code_obj:
                if not auth_code_obj.is_active:
                    raise PermissionError('Authorization code is inactive.')
                if auth_code_obj.max_usage > 0 and auth_code_obj.current_usage >= auth_code_obj.max_usage:
                    raise PermissionError(
                        'Authorization code usage limit reached.')

                # Increment usage
                auth_code_obj.current_usage += 1
                # Save is done at the end
            elif code != self.course_code:
                # If code is provided but not found in auth_codes AND not equal to course_code
                # (This case might happen if get_by_code found it but then it was removed?
                # Or if join_by_code called directly)
                # But get_by_code uses Q logic.
                pass

        # Add user to course as student
        username = user_wrapper.username
        self.student_nicknames[username] = username
        self.add_user(user_wrapper.obj)
        self.save()
        return True

    @classmethod
    def add_course(cls, course, teacher, color=None, emoji=None):
        if re.match(r'^[\w._\- ]+$', course) is None:
            raise ValueError
        teacher = User(teacher)
        if not teacher:
            raise engine.DoesNotExist('User')
        if teacher.role >= Role.STUDENT:
            raise PermissionError(
                f'{teacher} is not permitted to create a course')
        # HACK: not sure why the unique index is not work during the test
        if cls(course):
            raise engine.NotUniqueError('Course')

        if color and not re.match(r'^#[0-9a-fA-F]{6}$', color):
            raise ValueError('Invalid color format')
        if emoji and len(emoji) > 8:
            raise ValueError('Emoji too long')

        co = cls.engine(
            course_name=course,
            teacher=teacher.obj,
            course_code=cls.generate_course_code(),
            color=color,
            emoji=emoji,
        ).save()
        cls(co).add_user(teacher.obj)
        return True

    @classmethod
    def get_public(cls):
        if not cls('Public'):
            cls.add_course('Public', 'first_admin')
        return cls('Public')

    def own_permission(self, user) -> Permission:
        ROLE_CAPABILITY = {
            0:
            self.Permission(0),
            1:
            self.Permission.VIEW | self.Permission.SCORE,
            2:
            self.Permission.VIEW | self.Permission.GRADE,
            3:
            self.Permission.VIEW | self.Permission.GRADE
            | self.Permission.MODIFY,
            4:
            self.Permission.VIEW | self.Permission.GRADE
            | self.Permission.MODIFY,
        }

        role = perm(self.obj, user)

        return ROLE_CAPABILITY[role]

    def permission(self, user, req) -> bool:
        """
        check whether user own `req` permission
        """

        return bool(self.own_permission(user) & req)

    def get_ai_settings(self) -> Dict[str, Any]:
        """
        Get AI settings for the course.
        """
        model_name = DEFAULT_AI_MODEL
        if self.obj.ai_model:
            model_name = self.obj.ai_model.name

        return {
            "is_ai_enabled": self.obj.is_ai_vt_enabled,
            "ai_model": model_name
        }
