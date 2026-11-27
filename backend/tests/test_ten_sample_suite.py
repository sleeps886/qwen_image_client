"""Local-only supplied sample manifest/protocol checks. No gateway invocation."""
import json
from pathlib import Path
import pytest
from backend.tools.check_supplied_ten_samples import TRIPLETS, TEMP, prepare
from backend.app.services.association import batch_snapshot as batch, sequential_runner as seq
from backend.app.services.association.spatial_card_protocol import wire_for, decode_frame


def test_manifest_has_ten_separate_triplets_and_all_files_exist():
    assert len(TRIPLETS)==10 and all(len(t)==3 for t in TRIPLETS)
    assert len({name for t in TRIPLETS for name in t})==30
    assert all((TEMP/name).is_file() for t in TRIPLETS for name in t)
    for names in TRIPLETS:
        assert all(name.startswith(f'btr_target_00{i}') for i,name in enumerate(names,1))


@pytest.mark.parametrize('index',[0,2,4,7,9])
def test_sanitized_frame_has_exactly_selected_scope_without_reference(index):
    work,selected,geometry,metadata=prepare(TRIPLETS[index])
    assert len(selected)==50 and len({c for ids in selected for c in ids})==100
    assert metadata['raw_observations']==len(work['points'])
    spec=seq.preview(work,selected,order='received',call_limit=55)
    spec.pop('context_digest');spec.pop('model_calls');batch.configure(spec,work,batch.MODE,50,50,2,4,True,True)
    run={'working':work,'cursor':0,'spec':spec,'baseline_units':[]}
    units,context=batch.context_for(run);frame=context['spatial_frame'];wire=wire_for(context)
    assert len(frame['current_units'])==50 and frame['history_units']==[]
    assert decode_frame(wire)==frame
    assert len(units)==50 and spec['model_call_upper_bound']==55
    text=json.dumps(wire,ensure_ascii=False)
    assert all(s not in text for s in ('I01-','I02-','I03-','reference.json','btr_target','browse_catalog'))
    assert sum(bool(u['candidates']) for u in frame['current_units'])==metadata['sample_groups_with_geometry']
