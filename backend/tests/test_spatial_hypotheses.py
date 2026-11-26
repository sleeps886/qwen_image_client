import copy
import json
import pytest
from backend.app.services.association.spatial_hypotheses import prepare_hypotheses, expand_model_selection, partition_proposals, expand_partition_selection
from backend.tools.check_real_candidate_spatial import validate_selection, validate_worker


def units():
    return [{'unit_id': f'U{i}', 'platform_id': 'A', 't': i*10,
        'candidates': [{'branch_ref': 'pp', 'x': 100+i*40, 'y': 500+i*10},
                       {'branch_ref': 'mm', 'x': 100+i*40, 'y': -500-i*10}]}
        for i in range(8)]


def test_preparation_preserves_observations_and_retains_both_mirrors():
    rows = units(); before = copy.deepcopy(rows); doc = prepare_hypotheses(rows, trials=200)
    assert rows == before
    assert len(doc['hypotheses']) == 2 and doc['uncovered_units'] == []
    assert all(c['support_count'] == 8 and not c['identity_confirmed'] for c in doc['hypotheses'])
    assert {c['members'][0]['branch_ref'] for c in doc['hypotheses']} == {'pp', 'mm'}
    assert all(c['fit_rmse_m'] < 1e-8 for c in doc['hypotheses'])


def test_candidate_generation_ignores_source_fields_and_input_order():
    rows = units(); expected = prepare_hypotheses(rows, trials=200)
    for row in rows:
        row['source'] = 'SECRET_SOURCE'; row['strength'] = 123
    actual = prepare_hypotheses(list(reversed(rows)), trials=200)
    assert expected == actual and 'SECRET_SOURCE' not in json.dumps(actual)


def test_no_geometry_is_not_manufactured():
    rows = units(); rows[0]['candidates'] = []
    doc = prepare_hypotheses(rows, trials=200)
    assert doc['uncovered_units'] == ['U0']
    assert not any(m['unit_id'] == 'U0' for card in doc['hypotheses'] for m in card['members'])


def test_multiple_same_time_candidates_cannot_claim_same_piece():
    rows = units(); rows += [{**copy.deepcopy(rows[2]), 'unit_id': 'competing'}]
    doc = prepare_hypotheses(rows, trials=200)
    lookup = {u['unit_id']: u for u in rows}
    for card in doc['hypotheses']:
        times = [lookup[m['unit_id']]['t'] for m in card['members']]
        assert len(times) == len(set(times))


def context():
    rows = units()
    return {'snapshot_version': 0, 'history_units': [], 'history_assignments': [], 'existing_targets': [],
        'current_units': rows, 'candidate_evidence': prepare_hypotheses(rows, trials=200)}


def test_only_model_selected_card_expands_with_exact_branch():
    ctx = context(); card = ctx['candidate_evidence']['hypotheses'][1]
    obj = {'snapshot_version': 0, 'tracks': [{'target_ref': 'N1', 'hypothesis_refs': [card['hypothesis_ref']]}], 'unassigned_units': []}
    expanded = expand_model_selection(obj, ctx)
    assert {r['branch_ref'] for r in expanded['assignments']} == {card['members'][0]['branch_ref']}
    assert len(validate_selection(obj, ctx)) == 8


def test_no_implicit_best_card_and_no_missing_units():
    with pytest.raises(ValueError, match='漏掉组'):
        expand_model_selection({'snapshot_version': 0, 'tracks': [], 'unassigned_units': []}, context())


def test_model_cannot_choose_both_mirrors_as_two_targets():
    ctx = context(); a, b = ctx['candidate_evidence']['hypotheses']
    obj = {'snapshot_version': 0, 'tracks': [
        {'target_ref': 'N1', 'hypothesis_refs': [a['hypothesis_ref']]},
        {'target_ref': 'N2', 'hypothesis_refs': [b['hypothesis_ref']]}], 'unassigned_units': []}
    with pytest.raises(ValueError, match='重复占用'):
        expand_model_selection(obj, ctx)


def test_card_reference_hallucination_and_worker_scope_rejected():
    ctx = context()
    with pytest.raises(ValueError, match='不存在'):
        expand_model_selection({'snapshot_version': 0, 'tracks': [{'target_ref': 'N1', 'hypothesis_refs': ['L999']}], 'unassigned_units': []}, ctx)
    with pytest.raises(ValueError, match='allowed_hypothesis_refs'):
        validate_worker({'snapshot_version': 0, 'hypothesis_ref': 'L999', 'reason_code': 'continuity'}, {**ctx, 'allowed_hypothesis_refs': ['L001']})


def test_duplicates_and_nonfinite_input_rejected():
    rows = units()
    with pytest.raises(ValueError, match='重复'):
        prepare_hypotheses(rows+[rows[0]])
    rows[0]['candidates'][0]['x'] = float('inf')
    with pytest.raises(ValueError, match='非有限'):
        prepare_hypotheses(rows)


def test_shared_measured_turning_point_is_not_dropped_or_double_assigned():
    rows = [{'unit_id': f'U{i}', 'platform_id': 'A', 't': i*10,
        'candidates': [{'branch_ref': 'pp', 'x': 100+40*min(i, 4), 'y': 500+40*max(0, i-4)}]}
        for i in range(9)]
    evidence = prepare_hypotheses(rows, trials=600)
    plans = partition_proposals(evidence, rows)
    assert plans[0]['coverage'] == 9 and len(plans[0]['chains']) == 1
    assert any(j['shared_measured_boundary'] for j in plans[0]['joins'])
    ctx = {'snapshot_version': 0, 'current_units': rows, 'history_units': [], 'history_assignments': [],
        'existing_targets': [], 'candidate_evidence': evidence, 'partition_proposals': plans}
    obj = {'snapshot_version': 0, 'partition_ref': plans[0]['partition_ref'],
        'chain_bindings': [{'chain_ref': c['chain_ref'], 'target_ref': 'N1'} for c in plans[0]['chains']]}
    assert len(expand_partition_selection(obj, ctx)['assignments']) == 9
    assert len(validate_selection(obj, ctx)) == 9


def test_candidate_count_not_hardcoded_to_three_targets():
    rows = []
    for k in range(4):
        for i in range(8):
            rows.append({'unit_id': f'U{k}_{i}', 'platform_id': 'A', 't': i*10,
                'candidates': [{'branch_ref': 'pp', 'x': 100+i*40, 'y': 1000*k+10*i}]})
    evidence = prepare_hypotheses(rows, trials=1200)
    plans = partition_proposals(evidence, rows)
    assert plans[0]['coverage'] == 32 and len(plans[0]['chains']) == 4


def test_reject_all_is_valid_uncertainty_not_automatic_best_plan():
    ctx = context(); ctx['partition_proposals'] = partition_proposals(ctx['candidate_evidence'], ctx['current_units'])
    result = validate_selection({'snapshot_version': 0, 'reject_all': True}, ctx)
    assert all(row['target_ref'] is None and row['branch_ref'] is None for row in result.values())
