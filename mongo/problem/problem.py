import json
import enum
import copy
from hashlib import md5
from datetime import datetime, timedelta
from typing import (
    Any,
    BinaryIO,
    Dict,
    List,
    Optional,
)
from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile
from pathlib import Path
from mongo.utils import generate_ulid
from .. import engine
from ..base import MongoBase
from ..course import *
from ..utils import (RedisCache, doc_required, drop_none, MinioClient)
from ..user import User
from .exception import BadTestCase
from .test_case import (
    SimpleIO,
    ContextIO,
    IncludeDirectory,
    TestCaseRule,
)

__all__ = ('Problem', )


@dataclass
class UploadInfo:
    urls: List[str]
    upload_id: str


class Problem(MongoBase, engine=engine.Problem):

    class Permission(enum.IntFlag):
        VIEW = enum.auto()  # user view permission
        ONLINE = enum.auto()  # user can view problem or not
        MANAGE = enum.auto()  # user manage problem permission

    def detailed_info(self, *ks, **kns) -> Dict[str, Any]:
        '''
        return detailed info about this problem. notice
        that the `input` and `output` of problem test
        case won't be sent to front end, need call other
        route to get this info.

        Args:
            ks (*str): the field name you want to get
            kns (**[str, str]):
                specify the dict key you want to store
                the data get by field name
        Return:
            a dict contains problem's data
        '''
        if not self:
            return {}
        # problem -> dict
        _ret = self.to_mongo()
        # preprocess fields
        # case zip can not be serialized
        if 'caseZip' in _ret['testCase']:
            del _ret['testCase']['caseZip']
        # skip minio path
        if 'caseZipMinioPath' in _ret['testCase']:
            del _ret['testCase']['caseZipMinioPath']
        # convert couse document to course name
        _ret['courses'] = [course.course_name for course in self.courses]
        ret = {}
        for k in ks:
            kns[k] = k
        for k, n in kns.items():
            s_ns = n.split('__')
            # extract wanted value
            v = _ret[s_ns[0]]
            for s_n in s_ns[1:]:
                v = v[s_n]
            # extract wanted keys
            e = ret
            s_ks = k.split('__')
            for s_k in s_ks[:-1]:
                if s_k not in e:
                    e[s_k] = {}
                e = e[s_k]
            e[s_ks[-1]] = v
        return ret

    def allowed(self, language):
        if self.problem_type == 2:
            return True
        if language >= 3 or language < 0:
            return False
        return bool((1 << language) & self.allowed_language)

    @property
    def trial_mode_enabled(self) -> bool:
        """Check if trial mode is enabled for this problem."""
        # Check new field name first
        if hasattr(self.obj, 'trial_mode_enabled'):
            return getattr(self.obj, 'trial_mode_enabled', False)
        # Backward compatibility: check old field name
        if hasattr(self.obj, 'test_mode_enabled'):
            old_value = getattr(self.obj, 'test_mode_enabled', False)
            # Migrate old field to new field
            if old_value:
                self.obj.trial_mode_enabled = True
                self.obj.save()
            return old_value
        # Check database field directly (for compatibility)
        if hasattr(self.obj, '_data'):
            db_data = self.obj._data
            if 'trialModeEnabled' in db_data:
                return db_data.get('trialModeEnabled', False)
            # Backward compatibility: check old DB field
            if 'testModeEnabled' in db_data:
                old_value = db_data.get('testModeEnabled', False)
                # Migrate old field to new field
                if old_value:
                    self.obj.trial_mode_enabled = True
                    self.obj.save()
                return old_value
        return False

    @property
    def trial_submission_quota(self) -> int:
        """Get trial submission quota for this problem."""
        return getattr(self.obj, 'trial_submission_quota', -1)

    @property
    def trial_submission_counts(self) -> Dict[str, int]:
        """Get trial submission counts for this problem."""
        return getattr(self.obj, 'trial_submission_counts', {})

    def submit_count(self, user: User) -> int:
        '''
        Calculate how many submissions the user has submitted to this problem.
        '''
        # reset quota if it's a new day
        if user.last_submit.date() != datetime.now().date():
            user.update(problem_submission={})
            return 0
        return user.problem_submission.get(str(self.problem_id), 0)

    def running_homeworks(self) -> List:
        from ..homework import Homework
        now = datetime.now()
        return [Homework(hw.id) for hw in self.homeworks if now in hw.duration]

    def is_valid_ip(self, ip: str):
        return all(hw.is_valid_ip(ip) for hw in self.running_homeworks())

    def get_submission_status(self) -> Dict[str, int]:
        pipeline = {
            "$group": {
                "_id": "$status",
                "count": {
                    "$sum": 1
                },
            }
        }
        cursor = engine.Submission.objects(problem=self.id).aggregate(
            [pipeline], )
        return {item['_id']: item['count'] for item in cursor}

    def get_ac_user_count(self) -> int:
        ac_users = engine.Submission.objects(
            problem=self.id,
            status=0,
        ).distinct('user')
        return len(ac_users)

    def get_tried_user_count(self) -> int:
        tried_users = engine.Submission.objects(
            problem=self.id, ).distinct('user')
        return len(tried_users)

    @doc_required('user', User)
    def high_score_key(self, user: User) -> str:
        return f'PROBLEM_{self.id}_{user.id}_HIGH_SCORE'

    @doc_required('user', User)
    def get_high_score(self, user: User) -> int:
        '''
        Get highest score for user of this problem.
        '''

        cache = RedisCache()
        key = self.high_score_key(user=user)
        if (val := cache.get(key)) is not None:
            return int(val.decode())
        # TODO: avoid calling mongoengine API directly
        submissions = engine.Submission.objects(
            user=user.id,
            problem=self.id,
        ).only('score').order_by('-score').limit(1)
        if submissions.count() == 0:
            high_score = 0
        else:
            # It might < 0 if there is only incomplete submission
            high_score = max(submissions[0].score, 0)
        cache.set(key, high_score, ex=600)
        return high_score

    @doc_required('user', User)
    def own_permission(self, user: User) -> Permission:
        """
        generate user permission capability
        """

        user_cap = self.Permission(0)
        for course in map(Course, self.courses):
            # inherit course permission
            if course.permission(user, Course.Permission.VIEW):
                user_cap |= self.Permission.VIEW

            # online problem
            if self.problem_status == 0:
                check_public_problem = True
                for homework in course.homeworks:
                    if self.problem_id in homework.problem_ids:
                        check_public_problem = False
                        # current time after homework then online problem
                        if datetime.now() >= homework.duration.start:
                            user_cap |= self.Permission.ONLINE

                # problem does not belong to any homework
                if check_public_problem:
                    user_cap |= self.Permission.ONLINE

        # Admin, Teacher && is owner
        if user.role == 0 or self.owner == user.username:
            user_cap |= self.Permission.VIEW
            user_cap |= self.Permission.ONLINE
            user_cap |= self.Permission.MANAGE

        return user_cap

    def has_course_modify_permission(self, user: User) -> bool:
        """
        return True if the user can modify at least one course the
        problem belongs to. Typically used to allow course teachers
        to perform privileged problem actions.
        """
        return any(
            course.permission(user, Course.Permission.MODIFY)
            for course in map(Course, self.courses))

    def update_assets(
        self,
        user: Optional[User] = None,
        files_data: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ):
        '''
        Update problem assets and "merge" settings
        '''
        files_data = files_data or {}
        meta = meta or {}
        try:
            if files_data.get('case'):
                self.update_test_case(files_data['case'])

            minio_client = MinioClient()
            resource_files = {
                'custom_checker.py': ('checker', 'custom_checker.py'),
                'makefile.zip': ('makefile', 'makefile.zip'),
                'Teacher_file': ('teacher_file', 'Teacher_file'),
                'score.py': ('scoring_script', 'score.py'),
                'score.json': ('scoring_config', 'score.json'),
                'local_service.zip': ('local_service', 'local_service.zip'),
                'resource_data.zip': ('resource_data', 'resource_data.zip'),
                'resourcedata.zip': ('resource_data', 'resource_data.zip'),
                'resource_data_teacher.zip':
                ('resource_data_teacher', 'resource_data_teacher.zip'),
                'dockerfiles.zip': ('network_dockerfile', 'Dockerfiles.zip'),
            }

            meta = meta or {}
            pipeline_payload = meta.get('pipeline') or {}
            meta_config = meta.get('config') or {}
            current_config = copy.deepcopy(self.obj.config
                                           or Problem.config.default())
            current_config.update(meta_config)
            execution_mode = (pipeline_payload.get('executionMode') or
                              current_config.get('executionMode', 'general'))

            new_asset_paths = {}
            inferred_teacher_lang = None
            for key, (asset_type, filename) in resource_files.items():
                file_obj = files_data.get(key)
                if file_obj:
                    # Preserve original filename for Teacher_file to keep extension
                    stored_name = filename
                    if key == 'Teacher_file' and file_obj.filename:
                        stored_name = Path(file_obj.filename).name
                    if key == 'custom_checker.py':
                        try:
                            content = file_obj.read()
                            compile(content, stored_name, 'exec')
                            file_obj = BytesIO(content)
                        except SyntaxError as exc:
                            raise ValueError(
                                f'invalid custom checker syntax: {exc}')
                        except Exception as exc:
                            raise ValueError(
                                f'failed to read custom checker: {exc}')
                    path = self._save_asset_file(minio_client, file_obj,
                                                 asset_type, stored_name)
                    new_asset_paths[asset_type] = path
                    if key == 'Teacher_file':
                        ext = (Path(file_obj.filename or '').suffix
                               or '').lower().lstrip('.')
                        ext_map = {'c': 'c', 'cpp': 'cpp', 'py': 'py'}
                        if ext in ext_map:
                            inferred_teacher_lang = ext_map[ext]
                        elif execution_mode == 'interactive':
                            raise ValueError(
                                "interactive mode requires Teacher_file filename ending with .c/.cpp/.py to infer teacherLang"
                            )

            if new_asset_paths:
                current_asset_paths = current_config.get('assetPaths', {})
                current_asset_paths.update(new_asset_paths)
                if inferred_teacher_lang and current_asset_paths.get(
                        'teacherLang') is None:
                    current_asset_paths['teacherLang'] = inferred_teacher_lang
                current_config['assetPaths'] = current_asset_paths
            kwargs_for_edit = {}
            if meta_config or new_asset_paths:
                kwargs_for_edit['config'] = current_config
            if pipeline_payload:
                kwargs_for_edit['pipeline'] = pipeline_payload

            asset_paths = current_config.get('assetPaths', {})
            if execution_mode == 'functionOnly' and 'makefile' not in asset_paths:
                raise ValueError('functionOnly mode requires makefile.zip')
            if execution_mode == 'interactive' and 'teacher_file' not in asset_paths:
                raise ValueError('interactive mode requires Teacher_file')

            if kwargs_for_edit and user:
                self.edit_problem(user=user,
                                  problem_id=self.problem_id,
                                  **kwargs_for_edit)

        except BadZipFile as e:
            raise BadZipFile(f'Invalid zip file: {str(e)}')
        except Exception as e:
            raise ValueError(f'Failed to update assets: {str(e)}')

    def _save_asset_file(
        self,
        minio_client: MinioClient,
        file_obj: BinaryIO,
        asset_type: str,
        filename: str,
    ) -> str:

        if filename.endswith('.zip'):
            try:
                from zipfile import ZipFile
                file_obj.seek(0)
                ZipFile(file_obj).testzip()
            except Exception as e:
                raise BadZipFile(f'Invalid zip file {filename}: {str(e)}')

        path = f'problem/{self.problem_id}/{asset_type}/{filename}'

        file_obj.seek(0)
        file_data = file_obj.read()
        file_size = len(file_data)
        file_obj.seek(0)

        minio_client.client.put_object(
            minio_client.bucket,
            path,
            file_obj,
            file_size,
            part_size=5 * 1024 * 1024,
        )
        return path

    def permission(self, user: User, req: Permission) -> bool:
        """
        check whether user own `req` permission
        """

        return (self.own_permission(user=user) & req) == req

    @classmethod
    def get_problem_list(
        cls,
        user,
        offset: int = 0,
        count: int = -1,
        problem_id: int = None,
        name: str = None,
        tags: list = None,
        course: str = None,
    ):
        '''
        get a list of problems
        '''
        if course is not None:
            course = Course(course)
            if not course:
                return []
            course = course.obj
        # qurey args
        ks = drop_none({
            'problem_id': problem_id,
            'problem_name': name,
            'courses': course,
            'tags__in': tags,
        })
        problems = [
            p for p in engine.Problem.objects(**ks).order_by('problemId')
            if cls(p).permission(user=user, req=cls.Permission.ONLINE)
        ]
        # truncate
        if offset < 0 or (offset >= len(problems) and len(problems)):
            raise IndexError
        right = len(problems) if count < 0 else offset + count
        right = min(len(problems), right)
        return problems[offset:right]

    @classmethod
    def add(
        cls,
        user: User,
        courses: List[str],
        problem_name: str,
        status: Optional[int] = None,
        description: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        type: Optional[int] = None,
        test_case_info: Optional[Dict[str, Any]] = None,
        can_view_stdout: bool = False,
        allowed_language: Optional[int] = None,
        quota: Optional[int] = None,
        default_code: Optional[str] = None,
        config: Optional[dict] = None,
        pipeline: Optional[dict] = None,
        Trial_Mode: Optional[dict] = None,
        **kwargs,
    ):
        if len(courses) == 0:
            raise ValueError('No course provided')
        course_objs = []
        for course in map(Course, courses):
            if not course:
                raise engine.DoesNotExist
            course_objs.append(course.id)
        config = config or {}
        pipeline = pipeline or {}
        trial_mode = Trial_Mode or {}
        if 'scoringScript' in pipeline and 'scoringScrip' not in pipeline:
            pipeline['scoringScrip'] = pipeline['scoringScript']
        static_analysis_cfg = (config.get('staticAnalysis')
                               or config.get('staticAnalys')
                               or pipeline.get('staticAnalysis'))
        if static_analysis_cfg is not None:
            static_analysis_cfg = copy.deepcopy(static_analysis_cfg)
        else:
            static_analysis_cfg = {'custom': False}
        if config.get('networkAccessRestriction'):
            static_analysis_cfg.setdefault(
                'networkAccessRestriction',
                config['networkAccessRestriction'],
            )
        full_config = {
            'compilation': config.get('compilation', False),
            'testMode': trial_mode.get('Enabled', False),
            'aiVTuber': config.get('aiVTuber', False),
            'acceptedFormat': config.get('acceptedFormat', 'code'),
            'staticAnalys': static_analysis_cfg,
            'staticAnalysis': static_analysis_cfg,
            'artifactCollection': config.get('artifactCollection', []),
            'allowRead': pipeline.get('allowRead', False),
            'allowWrite': pipeline.get('allowWrite', False),
            'executionMode': pipeline.get('executionMode', 'general'),
            'customChecker': pipeline.get('customChecker', False),
            'teacherFirst': pipeline.get('teacherFirst', False),
            'scoringScript': pipeline.get('scoringScrip', {'custom': False}),
            'testModeQuotaPerStudent': trial_mode.get('Quota_Per_Student', 0),
        }
        for key in (
                'aiVTuberMaxToken',
                'aiVTuberMode',
                'maxStudentZipSizeMB',
                'networkAccessRestriction',
        ):
            if key in config and config[key] is not None:
                full_config[key] = config[key]
        known_config_keys = set(full_config.keys())
        for key, value in config.items():
            if value is None or key in known_config_keys:
                continue
            full_config[key] = value

        description_dict = description or {}
        problem_args = drop_none({
            'courses': course_objs,
            'problem_status': status,
            'problem_type': type,
            'problem_name': problem_name,
            'owner': user.username,
            'tags': tags,
            'quota': quota,
            'default_code': default_code,
            'config': full_config,
        })
        # Create ProblemDescription for the embedded document field
        if description_dict:
            problem_args['description'] = engine.ProblemDescription(
                description=description_dict.get('description', ''),
                input=description_dict.get('input', ''),
                output=description_dict.get('output', ''),
                hint=description_dict.get('hint', ''),
                sample_input=description_dict.get('sampleInput', []),
                sample_output=description_dict.get('sampleOutput', []),
            )
        problem = cls.engine(**problem_args).save()
        programming_problem_args = drop_none({
            'test_case':
            test_case_info,
            'can_view_stdout':
            can_view_stdout,
            'allowed_language':
            allowed_language,
        })
        if programming_problem_args and type != 2:
            problem.update(**programming_problem_args)
        return problem.problem_id

    @classmethod
    def edit_problem(
        cls,
        user: User,
        problem_id: int,
        **kwargs,
    ):
        """
        Edit existing problem (partial update)
        """
        from mongo import Course

        def _sync_config_aliases(cfg: dict):
            if 'staticAnalysis' in cfg and 'staticAnalys' not in cfg:
                cfg['staticAnalys'] = cfg['staticAnalysis']
            if 'staticAnalys' in cfg and 'staticAnalysis' not in cfg:
                cfg['staticAnalysis'] = cfg['staticAnalys']
            if 'scoringScrip' in cfg and 'scoringScript' not in cfg:
                cfg['scoringScript'] = cfg['scoringScrip']

        # Convert parameter names to match database field names
        if 'status' in kwargs:
            kwargs['problem_status'] = kwargs.pop('status')
        if 'type' in kwargs:
            kwargs['problem_type'] = kwargs.pop('type')

        problem = cls(problem_id)
        if not problem.obj:
            raise engine.DoesNotExist(f'Problem {problem_id} not found')

        if not problem.permission(user, cls.Permission.MANAGE):
            raise PermissionError(
                'Not enough permission to manage this problem')

        if 'courses' in kwargs and kwargs.get('courses') is not None:
            course_objs = []
            for name in kwargs['courses']:
                if not (course := Course(name)):
                    raise engine.DoesNotExist(f'Course {name} not found')
                course_objs.append(course.obj)
            kwargs['courses'] = course_objs

        if 'description' in kwargs and kwargs.get('description') is not None:
            desc = kwargs.pop('description')
            # Handle both dict and ProblemDescription object
            if isinstance(desc, engine.ProblemDescription):
                # Already a ProblemDescription, just use it
                kwargs['description'] = desc
            elif isinstance(desc, dict):
                # Convert dict to ProblemDescription
                kwargs['description'] = engine.ProblemDescription(
                    description=desc.get('description', ''),
                    input=desc.get('input', ''),
                    output=desc.get('output', ''),
                    hint=desc.get('hint', ''),
                    sample_input=desc.get('sampleInput', []),
                    sample_output=desc.get('sampleOutput', []),
                )

        if 'config' in kwargs or 'pipeline' in kwargs or 'Trial_Mode' in kwargs:
            full_config = problem.obj.config or {}

            if 'config' in kwargs and kwargs.get('config') is not None:
                config_update = kwargs.pop('config')
                full_config.update(config_update)
                _sync_config_aliases(full_config)

                # Sync trial_mode_enabled from config.trialMode (frontend sends this)
                if 'trialMode' in config_update:
                    trial_mode_enabled = config_update['trialMode']
                    full_config['testMode'] = trial_mode_enabled
                    # Sync trial_mode_enabled database field
                    problem.obj.trial_mode_enabled = trial_mode_enabled

            if 'pipeline' in kwargs and kwargs.get('pipeline') is not None:
                pipeline = kwargs.pop('pipeline')
                if 'allowRead' in pipeline:
                    full_config['allowRead'] = pipeline['allowRead']
                if 'allowWrite' in pipeline:
                    full_config['allowWrite'] = pipeline['allowWrite']
                if 'executionMode' in pipeline:
                    full_config['executionMode'] = pipeline['executionMode']
                if 'customChecker' in pipeline:
                    full_config['customChecker'] = pipeline['customChecker']
                if 'teacherFirst' in pipeline:
                    full_config['teacherFirst'] = pipeline['teacherFirst']
                if 'scoringScript' in pipeline:
                    full_config['scoringScript'] = pipeline['scoringScript']
                    full_config['scoringScrip'] = pipeline['scoringScript']
                if 'scoringScrip' in pipeline:
                    full_config['scoringScript'] = pipeline['scoringScrip']
                if 'staticAnalysis' in pipeline:
                    full_config['staticAnalysis'] = pipeline['staticAnalysis']
                    full_config['staticAnalys'] = pipeline['staticAnalysis']

            if 'Trial_Mode' in kwargs and kwargs.get('Trial_Mode') is not None:
                trial_mode = kwargs.pop('Trial_Mode')
                if 'Enabled' in trial_mode:
                    full_config['testMode'] = trial_mode['Enabled']
                    # Sync trial_mode_enabled database field
                    problem.obj.trial_mode_enabled = trial_mode['Enabled']
                if 'Quota_Per_Student' in trial_mode:
                    full_config['testModeQuotaPerStudent'] = trial_mode[
                        'Quota_Per_Student']

            kwargs['config'] = full_config
            _sync_config_aliases(kwargs['config'])

        if 'test_case_info' in kwargs and kwargs.get(
                'test_case_info') is not None:
            test_case_info = kwargs.pop('test_case_info')

            problem_type = kwargs.get('type', problem.obj.problem_type)
            if problem_type != 2:
                score = sum(t['taskScore']
                            for t in test_case_info.get('tasks', []))
                if score != 100:
                    raise ValueError("Cases' scores should be 100 in total")

            tasks = []
            for task in test_case_info.get('tasks', []):
                tasks.append(
                    engine.ProblemCase(
                        task_score=task.get('taskScore', 0),
                        case_count=task.get('caseCount', 1),
                        memory_limit=task.get('memoryLimit', 256),
                        time_limit=task.get('timeLimit', 1000),
                    ))

            test_case = engine.ProblemTestCase(
                language=test_case_info.get('language', 0),
                fill_in_template=test_case_info.get('fillInTemplate', ''),
                tasks=tasks,
            )

            if problem.obj.test_case:
                test_case.case_zip = problem.obj.test_case.case_zip
                test_case.case_zip_minio_path = problem.obj.test_case.case_zip_minio_path

            kwargs['test_case'] = test_case

        problem.obj.update(**drop_none(kwargs))
        problem.obj.reload()
        return problem

    def update_test_case(self, test_case: BinaryIO):
        '''
        edit problem's testcase

        Args:
            test_case: testcase zip file
        Exceptions:
            zipfile.BadZipFile: if `test_case` is not a zip file
            ValueError: if test case is None or problem_id is invalid
            engine.DoesNotExist
        '''
        self._validate_test_case(test_case)
        test_case.seek(0)
        self._save_test_case_zip(test_case)

    def _save_test_case_zip(self, test_case: BinaryIO):
        '''
        save test case zip file
        '''
        minio_client = MinioClient()
        path = self._generate_test_case_obj_path()
        minio_client.client.put_object(
            minio_client.bucket,
            path,
            test_case,
            -1,
            part_size=5 * 1024 * 1024,
            content_type='application/zip',
        )
        self.update(test_case__case_zip_minio_path=path)
        self.reload('test_case')

    def _generate_test_case_obj_path(self):
        return f'problem-test-case/{generate_ulid()}.zip'

    def _validate_test_case(self, test_case: BinaryIO):
        '''
        validate test case, raise BadTestCase if invalid
        '''
        rules: List[TestCaseRule] = [
            IncludeDirectory(self, 'include'),
            IncludeDirectory(self, 'share'),
            # for backward compatibility
            IncludeDirectory(self, 'chaos'),
        ]
        for rule in rules:
            rule.validate(test_case)

        # Should only match one format
        rules = [
            SimpleIO(self, ['include/', 'share/', 'chaos/']),
            ContextIO(self),
        ]
        excs = []
        for rule in rules:
            try:
                rule.validate(test_case)
            except BadTestCase as e:
                excs.append(e)

        if len(excs) == 0:
            raise BadTestCase('ambiguous test case format')
        elif len(excs) == 2:
            raise BadTestCase(
                f'invalid test case format\n\n{excs[0]}\n\n{excs[1]}')

    @classmethod
    def copy_problem(cls, user, problem_id):
        problem = Problem(problem_id).obj
        engine.Problem(
            problem_status=problem.problem_status,
            problem_type=problem.problem_type,
            problem_name=problem.problem_name,
            description=problem.description,
            owner=user.username,
            tags=problem.tags,
            test_case=problem.test_case,
        ).save()

    @doc_required('target', Course, src_none_allowed=True)
    def copy_to(
        self,
        user: User,
        target: Optional[Course] = None,
        **override,
    ) -> 'Problem':
        '''
        Copy a problem to target course, hidden by default.

        Args:
            user (User): The user who execute this action and will become
                the owner of copied problem.
            target (Optional[Course] = None): The course this problem will
                be copied to, default to the first of origial courses.
            override: Override field values passed to `Problem.add`.
        '''
        target = self.courses[0] if target is None else target
        # Copied problem is hidden by default
        status = override.pop('status', Problem.engine.Visibility.HIDDEN)
        ks = dict(
            user=user,
            courses=[target.course_name],
            problem_name=self.problem_name,
            status=status,
            description=self.description.to_mongo(),
            tags=self.tags,
            type=self.problem_type,
            test_case_info=self.test_case.to_mongo(),
            can_view_stdout=self.can_view_stdout,
            allowed_language=self.allowed_language,
            quota=self.quota,
            default_code=self.default_code,
        )
        ks.update(override)
        copy = self.add(**ks)
        return copy

    @classmethod
    def release_problem(cls, problem_id):
        course = Course('Public').obj
        problem = Problem(problem_id).obj
        problem.courses = [course]
        problem.owner = 'first_admin'
        problem.save()

    def is_test_case_ready(self) -> bool:
        return (self.test_case.case_zip.grid_id is not None
                or self.test_case.case_zip_minio_path is not None)

    def get_test_case(self) -> BinaryIO:
        if self.test_case.case_zip_minio_path is not None:
            minio_client = MinioClient()
            try:
                resp = minio_client.client.get_object(
                    minio_client.bucket,
                    self.test_case.case_zip_minio_path,
                )
                return BytesIO(resp.read())
            finally:
                if 'resp' in locals():
                    resp.close()
                    resp.release_conn()

        # fallback to legacy GridFS storage
        return self.test_case.case_zip

    def migrate_gridfs_to_minio(self):
        '''
        migrate test case from gridfs to minio
        '''
        if self.test_case.case_zip.grid_id is None:
            self.logger.info(
                f"no test case to migrate. problem={self.problem_id}")
            return

        if self.test_case.case_zip_minio_path is None:
            self.logger.info(
                f"uploading test case to minio. problem={self.problem_id}")
            self._save_test_case_zip(self.test_case.case_zip)
            self.logger.info(
                f"test case uploaded to minio. problem={self.problem_id} path={self.test_case.case_zip_minio_path}"
            )

        if self.check_test_case_consistency():
            self.logger.info(
                f"removing test case in gridfs. problem={self.problem_id}")
            self._remove_test_case_in_mongodb()
        else:
            self.logger.warning(
                f"data inconsistent after migration, keeping test case in gridfs. problem={self.problem_id}"
            )

    def _remove_test_case_in_mongodb(self):
        self.test_case.case_zip.delete()
        self.save()
        self.reload('test_case')

    def check_test_case_consistency(self):
        minio_client = MinioClient()
        try:
            resp = minio_client.client.get_object(
                minio_client.bucket,
                self.test_case.case_zip_minio_path,
            )
            minio_data = resp.read()
        finally:
            if 'resp' in locals():
                resp.close()
                resp.release_conn()

        gridfs_data = self.test_case.case_zip.read()
        if gridfs_data is None:
            self.logger.warning(
                f"gridfs test case is None but proxy is not updated. problem={self.problem_id}"
            )
            return False

        minio_checksum = md5(minio_data).hexdigest()
        gridfs_checksum = md5(gridfs_data).hexdigest()

        self.logger.info(
            f"calculated minio checksum. problem={self.problem_id} checksum={minio_checksum}"
        )
        self.logger.info(
            f"calculated gridfs checksum. problem={self.problem_id} checksum={gridfs_checksum}"
        )

        return minio_checksum == gridfs_checksum

    # TODO: hope minio SDK to provide more high-level API
    def generate_urls_for_uploading_test_case(
        self,
        length: int,
        part_size: int,
    ) -> UploadInfo:
        # TODO: update url after uploading completed
        # TODO: handle failed uploading
        path = self._generate_test_case_obj_path()
        self.update(test_case__case_zip_minio_path=path)

        minio_client = MinioClient()
        upload_id = minio_client.client._create_multipart_upload(
            minio_client.bucket,
            path,
            headers={'Content-Type': 'application/zip'},
        )
        part_count = (length + part_size - 1) // part_size

        def get(i: int):
            return minio_client.client.get_presigned_url(
                'PUT',
                minio_client.bucket,
                path,
                expires=timedelta(minutes=30),
                extra_query_params={
                    'partNumber': str(i + 1),
                    'uploadId': upload_id
                },
            )

        return UploadInfo(
            urls=[get(i) for i in range(part_count)],
            upload_id=upload_id,
        )

    def complete_test_case_upload(self, upload_id: str, parts: list):
        minio_client = MinioClient()
        minio_client.client._complete_multipart_upload(
            minio_client.bucket,
            self.test_case.case_zip_minio_path,
            upload_id,
            parts,
        )

        try:
            test_case = self.get_test_case()
            self._validate_test_case(test_case)
        except BadTestCase:
            self.update(test_case__case_zip_minio_path=None)
            raise
