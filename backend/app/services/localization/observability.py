"""Engineering range-sensitivity diagnostics, not calibrated confidence bounds."""
import numpy as np
from backend.app.services.localization.batch_inverse import evaluate_path,predicted_bearings,reference_for
from backend.app.services.localization.forward_observation import array_location


def range_sensitivity(model,candidate,evidence,context):
    reference=reference_for(context);platform=context['platforms'][0]
    times=np.linspace(candidate['start_t'],candidate['end_t'],33)
    xy=evaluate_path(model['parameters'],times,model['model'],model['t0'],model['span'])
    bearings=[]
    for array in ('hull','tow'):
        centers=np.array([array_location(platform,platform[array+'_m'],float(t),reference) for t in times])
        bearings.append(predicted_bearings(xy,centers,platform['heading_deg'],reference))
    lookup={p['id']:p for image in evidence['images'] for p in image['candidates']}
    errors=[lookup[cid].get('error_deg',.3) for cid in candidate['supported_measurement_ids'] if cid in lookup]
    error=max(.08,float(np.median(errors)) if errors else .3)
    parallax=float(np.median(abs(bearings[0]-bearings[1])))
    ratio=error/max(.001,parallax)
    return {'median_folded_parallax_deg':parallax,'angular_error_proxy_deg':error,
        'error_to_parallax_ratio':ratio,'range_sensitive':bool(ratio>=.5),
        'is_statistical_confidence':False,'method':'pixel_error_proxy_over_folded_parallax'}
