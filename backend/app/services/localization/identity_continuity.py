"""Consolidate fragments belonging to the same global dual-array motion hypothesis."""
import copy
from collections import defaultdict
from backend.app.services.localization.gap_estimates import estimate_model_gaps


def consolidate_identity_tracks(tracks,context):
    groups=defaultdict(list)
    originals=[]
    for track in tracks:
        if 'observation_segments' in track:
            originals.extend({**track,'branch':segment['branch'],'points':segment['points']}
                             for segment in track['observation_segments'])
        else:originals.append(track)
    for index,track in enumerate(originals):
        key=(track['id'],track.get('platform_id'),track.get('association_hypothesis_id'),track.get('mirror_variant'))
        # Never infer continuity from a model name, colour or target count alone.
        if not key[2] or type(key[3]) is not int or not track.get('model_parameters'):key=('unresolved',index)
        groups[key].append(track)
    output=[];associations=[]
    for group in groups.values():
        ordered=sorted(group,key=lambda track:track['points'][0]['t'])
        links={(gap['start_t'],gap['end_t']):gap for gap in estimate_model_gaps(ordered,context)}
        chains=[];current=[ordered[0]]
        for track in ordered[1:]:
            previous=current[-1]
            key=(previous['points'][-1]['t'],track['points'][0]['t'])
            if key in links and previous['model_parameters']==track['model_parameters']:
                current.append(track)
            else:chains.append(current);current=[track]
        chains.append(current)
        for chain in chains:
            merged=copy.deepcopy(chain[0]);points=[];intervals=[]
            for index,segment in enumerate(chain):
                if index:
                    start=chain[index-1]['points'][-1]['t'];end=segment['points'][0]['t']
                    gap=links[start,end]
                    points.extend({**point,'observation_supported':False} for point in gap['points'][1:-1])
                    intervals.append({'start_t':start,'end_t':end,'point_source':'motion_model_gap_prediction'})
                points.extend({**point,'observation_supported':True} for point in segment['points'])
            merged['points']=points
            merged['observation_segments']=[{'branch':segment.get('branch','main'),
                'points':copy.deepcopy(segment['points'])} for segment in chain]
            merged['inferred_intervals']=intervals
            merged['continuity']={'method':'same_global_dual_array_motion_hypothesis',
                'source_fragment_count':len(chain),'identity_confirmed':False}
            if len(chain)>1:merged['branch']=f"{merged['motion_model']}:alternative{merged['mirror_variant']}:continuous"
            output.append(merged)
        associations.append({'id':group[0]['id'],'association_hypothesis_id':group[0].get('association_hypothesis_id'),
            'mirror_variant':group[0].get('mirror_variant'),'source_fragments':len(group),
            'output_tracks':len(chains),'status':'continuous_hypothesis' if len(chains)==1 else 'unresolved_fragments'})
    return output,{'method':'global_dual_array_identity_and_shared_motion_model',
        'source_fragment_count':len(originals),'output_track_count':len(output),
        'merged_fragment_count':len(originals)-len(output),'identity_confirmed':False,'associations':associations}
