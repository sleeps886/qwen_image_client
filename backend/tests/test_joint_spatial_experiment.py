"""Offline contract tests; never call a provider or load reference labels."""
import copy
import pytest
from backend.tools.check_real_joint_spatial import validate_merge, validate_worker


def context():
    def unit(uid, t, x):
        return {'unit_id': uid, 't': t, 'platform_id': 'A', 'candidates': [
            {'branch_ref': 'pp', 'x': x, 'y': 10}, {'branch_ref': 'mm', 'x': -x, 'y': 10}]}
    return {'snapshot_version': 1, 'existing_targets': ['T001'],
        'history_units': [unit('U1', 0, 0)],
        'history_assignments': [{'unit_id': 'U1', 'target_ref': 'T001', 'branch_ref': 'pp', 'reason_code': 'continuity'}],
        'current_units': [unit('U2', 1, 10), unit('U3', 1, 20)]}


def answer():
    return {'snapshot_version': 1, 'assignments': [
        {'unit_id': 'U2', 'target_ref': 'T001', 'branch_ref': 'pp', 'reason_code': 'continuity'},
        {'unit_id': 'U3', 'target_ref': 'N1', 'branch_ref': 'mm', 'reason_code': 'new_target'}], 'revisions': []}


def test_joint_choices_exact_and_input_unchanged():
    ctx = context(); before = copy.deepcopy(ctx)
    result = validate_merge(answer(), ctx)
    assert len(result) == 3 and result['U3']['branch_ref'] == 'mm'
    assert ctx == before


def test_reject_simultaneous_claim():
    obj = answer(); obj['assignments'][1]['target_ref'] = 'T001'
    with pytest.raises(ValueError, match='同时刻'):
        validate_merge(obj, context())


def test_reject_unknown_branch():
    obj = answer(); obj['assignments'][0]['branch_ref'] = 'pm'
    with pytest.raises(ValueError, match='实际候选'):
        validate_merge(obj, context())


def test_reject_jump_without_using_source_labels():
    ctx = context(); ctx['current_units'][0]['candidates'][0]['x'] = 5000
    with pytest.raises(ValueError, match='100m/s'):
        validate_merge(answer(), ctx)


def test_null_branch_keeps_identity_hypothesis_without_inventing_position():
    ctx = context(); obj = answer(); obj['assignments'][0]['branch_ref'] = None
    assert validate_merge(obj, ctx)['U2']['branch_ref'] is None


def test_missing_and_duplicate_units_rejected():
    obj = answer(); obj['assignments'].pop()
    with pytest.raises(ValueError, match='漏判'):
        validate_merge(obj, context())
    obj = answer(); obj['assignments'].append(copy.deepcopy(obj['assignments'][0]))
    with pytest.raises(ValueError, match='重复'):
        validate_merge(obj, context())


def test_worker_only_uses_focus_candidates():
    ctx = {**context(), 'focus_unit_id': 'U2'}
    obj = {'snapshot_version': 1, 'target_ref': 'T001', 'branch_ref': 'mm', 'reason_code': 'continuity'}
    assert validate_worker(obj, ctx)['branch_ref'] == 'mm'
    obj['branch_ref'] = 'pm'
    with pytest.raises(ValueError, match='候选'):
        validate_worker(obj, ctx)


def test_revisions_only_explicit_visible_history():
    obj = answer(); obj['revisions'] = [{'unit_id': 'future', 'target_ref': None, 'branch_ref': None, 'reason_code': 'competing'}]
    with pytest.raises(ValueError, match='越界'):
        validate_merge(obj, context())
