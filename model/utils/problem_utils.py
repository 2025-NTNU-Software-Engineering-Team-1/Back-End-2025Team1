import copy
from typing import Dict, Tuple
from mongo.problem import Problem


def build_config_and_pipeline(problem: Problem) -> Tuple[Dict, Dict]:
    """Return copies of config/pipeline fields for API responses."""
    raw_config = problem.config or {}
    config_payload = copy.deepcopy(raw_config)
    static_analysis = (config_payload.get('staticAnalysis')
                       or config_payload.get('staticAnalys') or {})
    config_payload['staticAnalysis'] = static_analysis
    config_payload['staticAnalys'] = static_analysis
    static_analysis.setdefault('custom', False)
    network_cfg = config_payload.get('networkAccessRestriction')
    if not network_cfg and static_analysis.get('networkAccessRestriction'):
        network_cfg = static_analysis['networkAccessRestriction']
        config_payload['networkAccessRestriction'] = network_cfg
    config_payload.setdefault('artifactCollection', [])
    config_payload.setdefault('acceptedFormat', 'code')
    config_payload.setdefault('compilation',
                              config_payload.get('compilation', False))
    config_payload['trialMode'] = config_payload.get(
        'trialMode', config_payload.get('testMode', False))
    pipeline_payload = {
        'fopen': config_payload.get('fopen', False),
        'fwrite': config_payload.get('fwrite', False),
        'executionMode': config_payload.get('executionMode', 'general'),
        'customChecker': config_payload.get('customChecker', False),
        'teacherFirst': config_payload.get('teacherFirst', False),
        'scoringScript': config_payload.get('scoringScript',
                                            {'custom': False}),
    }
    return config_payload, pipeline_payload


def build_static_analysis_rules(problem: Problem):
    """Transform libraryRestrictions config into sandbox rules payload."""
    config_payload = problem.config or {}
    static_cfg = (config_payload.get('staticAnalysis')
                  or config_payload.get('staticAnalys') or {})
    lib_cfg = static_cfg.get('libraryRestrictions')
    if not lib_cfg or not lib_cfg.get('enabled'):
        return None

    keys = ('syntax', 'imports', 'headers', 'functions')

    def _normalize(src):
        src = src or {}
        return {k: list(src.get(k, []) or []) for k in keys}

    whitelist = _normalize(lib_cfg.get('whitelist'))
    blacklist = _normalize(lib_cfg.get('blacklist'))

    def _has_items(pool):
        return any(pool[k] for k in keys)

    if _has_items(whitelist):
        mode = 'white'
        selected = whitelist
    elif _has_items(blacklist):
        mode = 'black'
        selected = blacklist
    else:
        return None

    return {
        'model': mode,
        'syntax': selected['syntax'],
        'imports': selected['imports'],
        'headers': selected['headers'],
        'functions': selected['functions'],
    }


def derive_build_strategy(problem: Problem, submission_mode: int,
                          execution_mode: str) -> str:
    """Decide build strategy based on submission/testcase mode and executionMode."""
    exec_mode = execution_mode or 'general'
    is_zip = submission_mode == 1
    if exec_mode == 'functionOnly':
        return 'makeFunctionOnly'
    if exec_mode == 'interactive':
        return 'makeInteractive' if is_zip else 'compile'
    # general (legacy zip -> makeNormal)
    if is_zip:
        return 'makeNormal'
    return 'compile'
