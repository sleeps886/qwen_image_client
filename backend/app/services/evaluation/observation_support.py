"""Keep inferred trajectory samples out of observation-based accuracy metrics."""


def observation_segments(track):
    if 'observation_segments' in track:
        return [segment['points'] for segment in track['observation_segments']]
    segments=[];part=[]
    for point in track['points']:
        if point.get('observation_supported') is False or point.get('point_source')=='motion_model_gap_prediction':
            if part:segments.append(part);part=[]
        else:part.append(point)
    if part:segments.append(part)
    return segments
