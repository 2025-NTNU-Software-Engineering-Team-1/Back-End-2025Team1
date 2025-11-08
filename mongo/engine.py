from mongoengine import *
from mongoengine import signals
import mongoengine
import os
import html
from enum import IntEnum
from datetime import datetime, timezone, timedelta
from zipfile import ZipFile, BadZipFile

__all__ = [*mongoengine.__all__]

TAIPEI_TIMEZONE = timezone(timedelta(hours=8))

MONGO_HOST = os.environ.get('MONGO_HOST', 'mongomock://localhost')

# FIXME: we should use config to check whether is in testing
if MONGO_HOST.startswith('mongomock'):
    import mongomock
    MONGO_HOST = MONGO_HOST.replace('mongomock', 'mongodb')
    connect(
        'normal-oj',
        host=MONGO_HOST,
        mongo_client_class=mongomock.MongoClient,
    )
else:
    connect('normal-oj', host=MONGO_HOST)


def handler(event):
    '''
    Signal decorator to allow use of callback functions as class decorators.
    reference: http://docs.mongoengine.org/guide/signals.html
    '''

    def decorator(fn):

        def apply(cls):
            event.connect(fn, sender=cls)
            return cls

        fn.apply = apply
        return fn

    return decorator


@handler(signals.pre_save)
def escape_markdown(sender, document):
    document.markdown = html.escape(document.markdown)


class ZipField(FileField):

    def __init__(self, max_size=0, **ks):
        super().__init__(**ks)
        self.max_size = max_size

    def validate(self, value):
        super().validate(value)
        # skip check
        if not value:
            return
        try:
            with ZipFile(value) as zf:
                # the size of original files
                size = sum(info.file_size for info in zf.infolist())
        except BadZipFile:
            self.error('Only accept zip file.')
        # no limit
        if self.max_size <= 0:
            return
        if size > self.max_size:
            self.error(
                f'{size} bytes exceed the max size limit ({self.max_size} bytes)'
            )


class IntEnumField(IntField):

    def __init__(self, enum: IntEnum, **ks):
        super().__init__(**ks)
        self.enum = enum

    def validate(self, value):
        choices = (*self.enum.__members__.values(), )
        if value not in choices:
            self.error(f'Value must be one of {choices}')


class Profile(EmbeddedDocument):
    displayed_name = StringField(
        db_field='displayedName',
        default='',
        max_length=16,
    )
    bio = StringField(
        max_length=64,
        required=True,
        default='',
    )


class EditorConfig(EmbeddedDocument):
    font_size = IntField(db_field='fontSize',
                         min_value=8,
                         max_value=72,
                         default=14)
    theme = StringField(
        default='default',
        choices=[
            "default", "base16-dark", "base16-light", "dracula", "eclipse",
            "material", "monokai"
        ],
    )
    indent_type = IntField(db_field='indentType', default=1, choices=[0, 1])
    tab_size = IntField(
        db_field='tabSize',
        default=4,
        min_value=1,
        max_value=8,
    )
    language = IntField(
        default=0,
        choices=[0, 1, 2],
    )


class Duration(EmbeddedDocument):
    start = DateTimeField(default=datetime.now)
    end = DateTimeField(default=datetime(2111, 10, 10))

    def __contains__(self, other) -> bool:
        if not isinstance(other, datetime):
            return False
        return self.start <= other <= self.end


class UploadPolicy(EmbeddedDocument):
    """
    UploadMode:
        Code: One code file. Normal execution code submission.
        Zip: A zip file containing multiple files. Suitable for complex projects. Need makefile.
        Function: Single function submission. Used in function-based problems.
        Interactive: Run with student's and teacher's binaries for interaction.
    """

    class UploadMode(IntEnum):
        CODE = 0
        ZIP = 1
        FUNCTION = 2
        INTERACTIVE = 3

    mode = IntEnumField(enum=UploadMode, required=True)
    required_files = ListField(StringField(max_length=256), default=list)

    # ====== Teacher Artifacts Path (Optional)======
    # MinIO path
    compile_artifacts_path = StringField(max_length=256,
                                         required=False,
                                         default='')
    static_analysis_artifacts_path = StringField(max_length=256,
                                                 required=False,
                                                 default='')
    judger_artifacts_path = StringField(max_length=256,
                                        required=False,
                                        default='')
    checker_artifacts_path = StringField(max_length=256,
                                         required=False,
                                         default='')
    scorer_artifacts_path = StringField(max_length=256,
                                        required=False,
                                        default='')
    # ==============================================


class Pipeline(EmbeddedDocument):
    """
    upload_policy        ：mode（code/zip/function）、requiredFiles（Makefile、a.out）、teacherArtifacts 路徑。
    network_policy       ：外網白/黑名單、Local service 允許列表。
    static_analysis_policies：黑白名單、JE 自動終止策略。
    interaction_config   ：教師/學生 Binary 名稱、執行順序、stdin/stdout 配置、Checker 需求。
    custom_scoring       ：是否啟用、自訂 Score.py 路徑、I/O 介面描述。
    artifact_manifest    ：可提供下載的產物種類。
    test_case_policy     ：命名規則（legacy/ssttnn）、允許的輸入/輸出模式（stdin/fopen、stdout/fwrite）。
    """


class User(Document):

    class Role(IntEnum):
        ADMIN = 0
        TEACHER = 1
        STUDENT = 2
        TA = 3  # Teacher Assistant

    username = StringField(max_length=16, required=True, primary_key=True)
    user_id = StringField(db_field='userId', max_length=24, required=True)
    user_id2 = StringField(db_field='userId2', max_length=24, default='')
    email = EmailField(required=True, unique=True, max_length=128)
    md5 = StringField(required=True, max_length=32)
    active = BooleanField(default=False)
    role = IntEnumField(default=Role.STUDENT, enum=Role)
    profile = EmbeddedDocumentField(Profile, default=Profile)
    editor_config = EmbeddedDocumentField(
        EditorConfig,
        db_field='editorConfig',
        default=EditorConfig,
        null=True,
    )
    courses = ListField(ReferenceField('Course'))
    submissions = ListField(ReferenceField('Submission'))
    last_submit = DateTimeField(default=datetime.min)
    AC_problem_ids = ListField(IntField(), default=list)
    AC_submission = IntField(default=0)
    submission = IntField(default=0)
    problem_submission = DictField(db_field='problemSubmission')

    @property
    def info(self):
        return {
            'username': self.username,
            'displayedName': self.profile.displayed_name,
            'md5': self.md5,
            'role': self.role,
        }


@escape_markdown.apply
class Homework(Document):

    homework_name = StringField(
        max_length=64,
        required=True,
        db_field='homeworkName',
        unique_with='course_id',
    )
    markdown = StringField(max_length=10000, default='')
    scoreboard_status = IntField(
        default=0,
        choices=[0, 1],
        db_field='scoreboardStatus',
    )
    course_id = StringField(required=True, db_field='courseId')
    duration = EmbeddedDocumentField(Duration, default=Duration)
    problem_ids = ListField(IntField(), db_field='problemIds')
    student_status = DictField(db_field='studentStatus')
    ip_filters = ListField(StringField(max_length=64), default=list)
    penalty = StringField(max_length=10000, default='score = 0')


class Course(Document):
    course_name = StringField(
        max_length=64,
        required=True,
        unique=True,
        db_field='courseName',
    )
    student_nicknames = DictField(db_field='studentNicknames')
    course_status = IntField(default=0, choices=[0, 1])
    teacher = ReferenceField('User')
    tas = ListField(ReferenceField('User'))
    homeworks = ListField(ReferenceField('Homework', reverse_delete_rule=PULL))
    announcements = ListField(ReferenceField('Announcement'))
    posts = ListField(ReferenceField('Post'), default=list)
    student_scores = DictField(db_field='studentScores')


class Number(Document):
    name = StringField(
        max_length=64,
        primary_key=True,
    )
    number = IntField(default=1)


class ProblemCase(EmbeddedDocument):
    task_score = IntField(required=True, db_field='taskScore')
    case_count = IntField(required=True, db_field='caseCount')
    memory_limit = IntField(required=True, db_field='memoryLimit')  # in KB
    time_limit = IntField(required=True, db_field='timeLimit')  # in ms


class ProblemTestCase(EmbeddedDocument):
    language = IntField(choices=[0, 1, 2])
    fill_in_template = StringField(db_field='fillInTemplate', max_length=16000)
    tasks = EmbeddedDocumentListField(
        ProblemCase,
        default=list,
    )
    submission_mode = IntField(
        choices=[0, 1],
        default=0,
        db_field='submissionMode',
    )
    # zip file contains testcase input/output
    case_zip = ZipField(
        db_field='caseZip',
        defautl=None,
        null=True,
    )
    case_zip_minio_path = StringField(
        null=True,
        max_length=256,
        db_field='caseZipMinioPath',
    )


class ProblemDescription(EmbeddedDocument):
    description = StringField(max_length=100000)
    input = StringField(max_length=100000)
    output = StringField(max_length=100000)
    hint = StringField(max_length=100000)
    sample_input = ListField(
        StringField(max_length=1024),
        default=list,
        db_field='sampleInput',
    )
    sample_output = ListField(
        StringField(max_length=1024),
        default=list,
        db_field='sampleOutput',
    )

    def escape(self):
        self.description, self.input, self.output, self.hint = (html.escape(
            v or '') for v in (
                self.description,
                self.input,
                self.output,
                self.hint,
            ))
        _io = zip(self.sample_input, self.sample_output)
        for i, (ip, op) in enumerate(_io):
            self.sample_input[i] = ip or html.escape(ip)
            self.sample_output[i] = op or html.escape(op)


@handler(signals.pre_save)
def problem_desc_escape(sender, document):
    document.description.escape()


@problem_desc_escape.apply
class Problem(Document):

    class Visibility:
        SHOW = 0
        HIDDEN = 1

    problem_id = SequenceField(
        db_field='problemId',
        required=True,
        primary_key=True,
    )
    courses = ListField(ReferenceField('Course'), default=list)
    problem_status = IntField(
        default=1,
        choices=[Visibility.SHOW, Visibility.HIDDEN],
        db_field='problemStatus',
    )
    problem_type = IntField(
        default=0,
        choices=[0, 1, 2],
        db_field='problemType',
    )
    problem_name = StringField(
        db_field='problemName',
        max_length=64,
        required=True,
    )
    description = EmbeddedDocumentField(
        ProblemDescription,
        default=ProblemDescription,
    )
    owner = StringField(max_length=16, required=True)
    # pdf =
    tags = ListField(StringField(max_length=16))
    test_case = EmbeddedDocumentField(
        ProblemTestCase,
        db_field='testCase',
        default=ProblemTestCase,
    )
    ac_user = IntField(db_field='ACUser', default=0)
    submitter = IntField(default=0)
    homeworks = ListField(ReferenceField('Homework'), default=list)
    # user can view stdout/stderr
    can_view_stdout = BooleanField(db_field='canViewStdout', default=True)
    cpp_report_url = StringField(
        db_field='cppReportUrl',
        default='',
        max_length=128,
    )
    python_report_url = StringField(
        db_field='pythonReportUrl',
        default='',
        max_length=128,
    )
    # moss_status (not started: 0, processing: 1, done: 2)
    moss_status = IntField(
        default=0,
        choices=[0, 1, 2],
        db_field='mossStatus',
    )
    # bitmask of allowed languages (c: 1, cpp: 2, py3: 4)
    allowed_language = IntField(db_field='allowedLanguage', default=7)
    # high score for each student
    # Dict[username, score]
    high_scores = DictField(db_field='highScore', default={})
    quota = IntField(default=-1)
    default_code = StringField(
        db_field='defaultCode',
        max_length=10**4,
        default='',
    )

    # === Test Mode Fields ===
    test_mode_enabled = BooleanField(db_field='testModeEnabled', default=False)
    test_submission_quota = IntField(
        db_field='testSubmissionQuota',
        default=-1  # -1 for unlimited
    )

    # Public test cases for Test Mode
    public_cases_zip = ZipField(
        db_field='publicCasesZip',
        default=None,
        null=True,
    )
    public_cases_zip_minio_path = StringField(
        null=True,
        max_length=256,
        db_field='publicCasesZipMinioPath',
    )

    # AC Code for Test Mode
    ac_code = ZipField(db_field='acCode', default=None, null=True)
    ac_code_minio_path = StringField(
        null=True,
        max_length=256,
        db_field='acCodeMinioPath',
    )
    ac_code_language = IntField(
        db_field='acCodeLanguage',
        null=True,
    )

    # Stats for Test Mode
    # Dict[username, count]
    test_submission_counts = DictField(db_field='testSubmissionCounts',
                                       default={})


class CaseResult(EmbeddedDocument):
    status = IntField(required=True)
    exec_time = IntField(required=True, db_field='execTime')
    memory_usage = IntField(required=True, db_field='memoryUsage')
    output = ZipField(
        required=True,
        null=True,
        max_size=11**9,
    )
    output_minio_path = StringField(
        null=True,
        max_length=256,
        db_field='outputMinioPath',
    )


class TaskResult(EmbeddedDocument):
    status = IntField(default=-1)
    exec_time = IntField(default=-1, db_field='execTime')
    memory_usage = IntField(default=-1, db_field='memoryUsage')
    score = IntField(default=0)
    cases = EmbeddedDocumentListField(CaseResult, default=list)


class BaseSubmissionDocument(Document):
    meta = {
        'abstract': True,
        'indexes': [
            'problem',
            'user',
            ('problem', 'user', '-timestamp'),
        ]
    }

    problem = ReferenceField(Problem, required=True)
    user = ReferenceField(User, required=True)
    language = IntField(
        required=True,
        min_value=0,
        max_value=3,
        db_field='languageType',
    )
    timestamp = DateTimeField(required=True)
    status = IntField(default=-2)
    score = IntField(default=-1)
    tasks = EmbeddedDocumentListField(TaskResult, default=list)
    exec_time = IntField(default=-1, db_field='runTime')
    memory_usage = IntField(default=-1, db_field='memoryUsage')
    code = ZipField(null=True, max_size=10**7)
    code_minio_path = StringField(
        null=True,
        max_length=256,
        db_field='codeMinioPath',
    )
    last_send = DateTimeField(db_field='lastSend', default=datetime.now)
    ip_addr = StringField(default=None, null=True)


class Submission(BaseSubmissionDocument):
    meta = {'indexes': [('problem', 'user'), ('problem', '-score')]}
    comment = FileField(default=None, null=True)


class TrialSubmission(BaseSubmissionDocument):
    """
    Document for Test Mode Submissions.
    These submissions are for testing against public/custom cases
    and do not affect homework scores.
    """
    meta = {
        'collection':
        'test_submission',
        'indexes': [
            'problem',
            'user',
            ('problem', 'user', '-timestamp'),
            {
                'fields': ['timestamp'],
                'expireAfterSeconds': 1209600  # 14 days Time-To-Live
            },
        ]
    }

    # True if using the problem's public test cases
    use_default_case = BooleanField(db_field='useDefaultCase', default=True)

    # Zip file of custom input cases (if use_default_case is False)
    custom_input = ZipField(
        null=True,
        max_size=10**7  # 10MB limit for custom input
    )
    custom_input_minio_path = StringField(
        null=True,
        max_length=256,
        db_field='customInputMinioPath',
    )


@escape_markdown.apply
class Message(Document):
    timestamp = DateTimeField(default=datetime.now)
    sender = StringField(max_length=16, required=True)
    receivers = ListField(StringField(max_length=16), required=True)
    status = IntField(default=0, choices=[0, 1])  # not delete / delete
    title = StringField(max_length=32, required=True)
    markdown = StringField(max_length=100000, required=True)


@escape_markdown.apply
class Announcement(Document):
    status = IntField(default=0, choices=[0, 1])  # not delete / delete
    title = StringField(max_length=64, required=True)
    course = ReferenceField('Course', required=True)
    create_time = DateTimeField(db_field='createTime', default=datetime.now)
    update_time = DateTimeField(db_field='updateTime', default=datetime.now)
    creator = ReferenceField('User', required=True)
    updater = ReferenceField('User', required=True)
    markdown = StringField(max_length=100000, required=True)
    pinned = BooleanField(default=False)


@escape_markdown.apply
class PostThread(Document):
    markdown = StringField(default='', required=True, max_length=100000)
    author = ReferenceField('User', db_field='author')
    course_id = ReferenceField('Course', db_field='courseId')
    depth = IntField(default=0)  # 0 is top post, 1 is reply to post
    created = DateTimeField(required=True)
    updated = DateTimeField(required=True)
    status = IntField(default=0, choices=[0, 1])  # not delete / delete
    reply = ListField(ReferenceField('PostThread', db_field='postThread'),
                      dafault=list)


class Post(Document):
    post_name = StringField(default='', required=True, max_length=64)
    thread = ReferenceField('PostThread', db_field='postThread')


class Config(Document):
    meta = {
        'allow_inheritance': True,
    }
    name = StringField(required=True, max_length=64, primary_key=True)


class Sandbox(EmbeddedDocument):
    name = StringField(required=True)
    url = StringField(required=True)
    token = StringField(required=True)


class SubmissionConfig(Config):
    rate_limit = IntField(default=0, db_field='rateLimit')
    sandbox_instances = EmbeddedDocumentListField(
        Sandbox,
        default=[
            Sandbox(
                name='Sandbox-0',
                url='http://sandbox:1450',
                token='KoNoSandboxDa',
            ),
        ],
        db_field='sandboxInstances',
    )


class LoginRecords(Document):
    user_id = StringField(required=True)
    ip_addr = StringField(required=True)
    success = BooleanField(required=True, default=False)
    timestamp = DateTimeField(required=True, default=datetime.now)


class PersonalAccessToken(Document):
    """
    Personal Access Token (PAT) Document.
    Collection name: 'personal_access_tokens'
    """

    meta = {
        'collection':
        'personal_access_tokens',
        'indexes': [
            'owner',  # Index for querying the owner's tokens
            '-created_time',  # Index for sorting by creation time (descending)
            'due_time',  # Index for sorting by expiration time
            'hash',  # Index for quick hash lookup
        ]
    }

    # === Core Attributes ===

    pat_id = StringField(max_length=64,
                         required=True,
                         primary_key=True,
                         db_field='id')  # PAT ID (Primary Key)
    hash = StringField(max_length=64, required=True,
                       db_field='hash')  # PAT Hash Value (SHA-256)
    name = StringField(max_length=128, required=True,
                       db_field='name')  # PAT Name
    owner = StringField(required=True)  # User ID or username who owns the PAT
    scope = ListField(StringField(),
                      required=True,
                      default=list,
                      db_field='scope')

    # === Time and Usage Tracking ===
    due_time = DateTimeField(
        required=False, db_field='dueTime',
        utc_timezone=True)  # The expiration time of the PAT
    created_time = DateTimeField(
        default=datetime.now(timezone.utc),
        required=True,
        db_field='createdTime')  # The time the PAT was created
    last_used_time = DateTimeField(
        required=False,
        db_field='lastUsedTime')  # The last time the PAT was used (Optional)
    last_used_scope = ListField(
        StringField(), required=False, db_field='lastUsedScope',
        default=list)  # The scope used during the last access (Optional)

    # === Revoke ===
    is_revoked = BooleanField(
        default=False)  # Revoked status by admin, cannot be changed by user
    revoked_by = StringField(
        required=False)  # Record who revoked the token (admin user ID)
    revoked_time = DateTimeField(
        required=False)  # Record the time of revocation

    description = StringField(
        required=False,
        max_length=256)  # Record the purpose of the token (Optional)
