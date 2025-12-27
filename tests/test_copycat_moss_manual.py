import os
import pathlib
import pytest
from mongo import engine, User, Problem

S_NAMES = {
    'student': 'Chika.Fujiwara',  # base.c base.py
    'student-2': 'Nico.Kurosawa',  # base.cpp base_2.py
}


@pytest.mark.skipif(
    os.environ.get('MOSS_USERID') is None,
    reason='Set MOSS_USERID to run the real MOSS integration test.',
)
def test_copycat_moss_integration(
    setup_minio,
    make_course,
    problem_ids,
    save_source,
    submit_once,
    monkeypatch,
):
    if not os.environ.get('MOSS_USERID'):
        pytest.skip('Set MOSS_USERID to run the real MOSS integration test.')

    # create course and problem
    from tests.utils.user import create_user
    create_user(username='teacher', role=1)
    for username in S_NAMES.keys():
        create_user(username=username, role=2)
    make_course(username='teacher', students=S_NAMES)
    pid = problem_ids('teacher', 1, True)[0]

    # save source code (for submit_once)
    src_dir = pathlib.Path('tests/src')
    exts = ['.c', '.cpp', '.py', '.pdf']
    for src in src_dir.iterdir():
        if any([not src.suffix in exts, not src.is_file()]):
            continue
        save_source(
            src.stem,
            src.read_bytes(),
            exts.index(src.suffix),
        )

    # submission
    name2code = {
        'student': [('base.c', 0), ('base.py', 2)],
        'student-2': [('base.cpp', 1), ('base_2.py', 2)]
    }
    for name, code in name2code.items():
        for filename, language in code:
            submit_once(
                name=name,
                pid=pid,
                filename=filename,
                lang=language,
            )
    # change all submissions to status 0 (Accepted)
    engine.Submission.objects.update(status=0)

    from model import copycat
    import mosspy

    class MossWithEnv(mosspy.Moss):

        def __init__(self, userid, lang, *args, **kwargs):
            userid = int(os.environ.get('MOSS_USERID', userid))
            super().__init__(userid, lang, *args, **kwargs)

    monkeypatch.setattr(copycat.mosspy, 'Moss', MossWithEnv)

    user = User('teacher')
    copycat.get_report_task(user, pid, S_NAMES)

    problem = Problem(pid)
    assert problem.moss_status == 2
    assert problem.cpp_report_url or problem.python_report_url
