"""Offline-only evaluator. Never imported by ingestion/evidence/model prompts."""
from collections import defaultdict
import numpy as np
from scipy.optimize import linear_sum_assignment


def evaluate_memberships(observations, truth, assignments):
    """Align opaque hypothesis IDs; do not score by label string equality.

    truth/assignments: observation_id -> physical target label. Missing assignment
    is unresolved, never silently removed from the denominator.
    """
    ids = {p['observation_id'] for p in observations}
    if set(truth) != ids or not set(assignments) <= ids:
        raise ValueError('评估必须覆盖全部真值，预测不能引用未知点')
    actual, predicted = sorted(set(truth.values())), sorted(set(assignments.values()))
    matrix = np.zeros((len(actual), len(predicted)), dtype=int)
    ai, pi = {x:i for i,x in enumerate(actual)}, {x:i for i,x in enumerate(predicted)}
    for oid, label in assignments.items():
        matrix[ai[truth[oid]], pi[label]] += 1
    match = dict()
    if matrix.size:
        rows, cols = linear_sum_assignment(-matrix)
        match = {predicted[c]: actual[r] for r, c in zip(rows, cols)}
    correct = sum(match.get(label) == truth[oid] for oid, label in assignments.items())
    merges = sum(len({truth[oid] for oid, value in assignments.items() if value == label})-1 for label in predicted)
    splits = sum(max(0, len({assignments[oid] for oid in ids if truth[oid] == label and oid in assignments})-1) for label in actual)
    streams = defaultdict(list)
    for p in observations:
        streams[(truth[p['observation_id']], p['platform_id'], p['array'])].append(p)
    switches, correct_links, wrong_links = 0, 0, 0
    for rows in streams.values():
        rows.sort(key=lambda p: p['event_time'])
        labels = [assignments.get(p['observation_id']) for p in rows]
        observed = [x for x in labels if x is not None]
        switches += sum(a != b for a, b in zip(observed, observed[1:]))
    predicted_streams = defaultdict(list)
    for point in observations:
        oid = point['observation_id']
        if oid in assignments:
            predicted_streams[(assignments[oid],point['platform_id'],point['array'])].append(point)
    for rows in predicted_streams.values():
        rows.sort(key=lambda p:p['event_time'])
        for a,b in zip(rows,rows[1:]):
            if truth[a['observation_id']] == truth[b['observation_id']]: correct_links += 1
            else: wrong_links += 1
    return {'total': len(ids), 'assigned': len(assignments), 'unresolved': len(ids)-len(assignments),
            'aligned_accuracy_all_points': correct/max(1,len(ids)), 'merge_excess': merges,
            'split_excess': splits, 'identity_switches': switches,
            'correct_continuations': correct_links, 'wrong_continuations': wrong_links,
            'label_alignment': match, 'evaluation_only': True}
