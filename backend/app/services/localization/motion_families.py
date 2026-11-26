"""Observed-bearing motion-family seeds; no labels or expected target count."""
import numpy as np
from scipy.optimize import least_squares
from backend.app.services.localization.batch_inverse import evaluate_path,predicted_bearings,reference_for
from backend.app.services.localization.forward_observation import array_location


def family_seeds(model,variants,candidate,images,context,check,*,explore_curvature=True,radial_scales=(.5,.75,1.5,2.)):
    reference=reference_for(context);platform=context['platforms'][0]
    ids=set(candidate['supported_measurement_ids'])
    records=[(image['array'],p) for image in images for p in image['candidates']
             if p['id'] in ids and p['sample_index']%5!=0]
    if len(records)<8:return []
    times=np.array([p['t'] for _,p in records]);angles=np.array([p['bearing_deg'] for _,p in records])
    centers=np.array([array_location(platform,platform[array+'_m'],float(p['t']),reference) for array,p in records])
    origin=np.array(reference.to_xy(platform['start_ll']))/1000
    candidates=[]
    families=[('cv','cv',()),('cv','constant_latitude',(3,)),('cv','constant_longitude',(2,))]
    if model['model']=='harmonic':families=[('harmonic','latitude_harmonic',(4,6)),('harmonic','longitude_harmonic',(5,7))]
    # A centroid-selected CV is an initialization, not a permanent model lock.
    # Decide whether to explore curvature using TRAINING observations only.
    promote=False
    # Multiple existing geometric branches seed the search; no reference path is supplied.
    for kind,family,excluded in families:
        seeds=[]
        starts=[]
        for previous in [model,*variants]:
            if kind=='harmonic':
                if previous['model']=='harmonic':starts.append(np.array(previous['parameters'],dtype=float))
                elif promote:
                    xy=evaluate_path(previous['parameters'],times,previous['model'],previous['t0'],previous['span'])
                    design=np.array([np.ones(len(times)),(times-model['t0'])/model['span']]).T
                    cv=np.linalg.lstsq(design,xy/1000,rcond=None)[0].ravel()
                    starts.extend(np.r_[cv,0.,0.,0.,0.,period] for period in (.5,.8,1.5))
            else:
                xy=evaluate_path(previous['parameters'],times,previous['model'],previous['t0'],previous['span'])
                design=np.array([np.ones(len(times)),(times-model['t0'])/model['span']]).T
                starts.append(np.linalg.lstsq(design,xy/1000,rcond=None)[0].ravel())
        for initial in starts:
            initial[list(excluded)]=0
            active=np.array([i for i in range(len(initial)) if i not in excluded])
            lo=np.full(len(initial),-100.);hi=np.full(len(initial),100.)
            lo[:2]=origin-100;hi[:2]=origin+100
            if kind=='harmonic':lo[-1]=.25;hi[-1]=4.
            initial=np.clip(initial,lo+1e-7,hi-1e-7)
            def expand(values):
                p=initial.copy();p[active]=values
                return p
            def residual(values):
                check();p=expand(values)
                xy=evaluate_path(p,times,kind,model['t0'],model['span'])
                return predicted_bearings(xy,centers,platform['heading_deg'],reference)-angles
            fit=least_squares(residual,initial[active],bounds=(lo[active],hi[active]),loss='soft_l1',
                              f_scale=.3,max_nfev=70,x_scale='jac')
            if fit.success:
                parameters=expand(fit.x)
                item={**model,'model':kind,'parameter_family':family,'parameters':parameters.tolist()}
                if all(np.linalg.norm(parameters-np.array(old['parameters']))>.01 for old in seeds):seeds.append(item)
        if explore_curvature and family=='cv' and seeds and len(records)>=30:
            errors=[]
            for item in seeds:
                xy=evaluate_path(item['parameters'],times,'cv',model['t0'],model['span'])
                delta=predicted_bearings(xy,centers,platform['heading_deg'],reference)-angles
                errors.append(float(np.sqrt(np.mean(delta**2))))
            promote=min(errors)>.25
            if promote:
                families += [('harmonic','latitude_harmonic',(4,6)),('harmonic','longitude_harmonic',(5,7))]
        if kind in ('cv','harmonic') and abs(platform['speed_kn'])<1e-8:
            # Bearing-only short-baseline ranges have shallow local minima.
            # Search bounded radial scales before image optimization, using
            # the same observations (not a geographic target-location prior).
            base=list(seeds)
            for item in base:
                for factor in radial_scales:
                    values=np.array(item['parameters'],dtype=float)
                    tail=values[-1:].copy() if kind=='harmonic' else np.zeros(0)
                    parameters=(values[:-1] if len(tail) else values).reshape(-1,2).copy()
                    parameters[0]=origin+factor*(parameters[0]-origin)
                    parameters[1:]*=factor
                    seeds.append({**item,'parameters':np.r_[parameters.ravel(),tail].tolist(),'radial_seed_scale':factor})
        candidates.extend((item,np.array([i for i in range(len(item['parameters'])) if i not in excluded])) for item in seeds)
    return candidates
