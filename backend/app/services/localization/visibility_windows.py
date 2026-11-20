"""Extend an existing identity's window only with independent dual-raster gain."""
import copy
import numpy as np
from backend.app.services.localization.batch_inverse import evaluate_path,predicted_bearings,reference_for
from backend.app.services.localization.forward_observation import array_location


def extend_visibility_windows(selected,evidence,context,control):
    from backend.app.services.localization.profile_inverse import nuisance_coefficients
    output=copy.deepcopy(selected)
    report={'method':'dual_raster_leave_one_identity_out_v1','uses_reference_labels':False,
            'minimum_amplitude':.5,'minimum_relative_gain':.1,'minimum_consecutive_rows':5,'updates':[]}
    if not selected:return output,report
    images=evidence['images']
    if {im['array'] for im in images}!={'hull','tow'} or any('_profile_values' not in im for im in images):
        return output,{**report,'status':'raster_profiles_unavailable'}
    reference=reference_for(context);platform=context['platforms'][0];records=[]
    for image in images:
        control.check();times=np.array([p['t'] for p in image['profiles']]);angles=image['_angles'][::2]
        values=np.asarray(image['_profile_values'][:,::2],dtype=float)
        centers=np.array([array_location(platform,platform[image['array']+'_m'],float(t),reference) for t in times])
        templates=[]
        for candidate in selected:
            model=candidate['variants'][0]
            xy=evaluate_path(model['parameters'],times,model['model'],model['t0'],model['span'])
            bearing=predicted_bearings(xy,centers,platform['heading_deg'],reference)
            width=candidate.get('profile_widths_deg',{}).get(image['array'],2.)
            templates.append(np.exp(-.5*((angles[None,:]-bearing[:,None])/width)**2))
        design=np.stack([np.ones_like(values),np.broadcast_to(np.cos(2*np.pi*angles/120.),values.shape),*templates],axis=2)
        weight=((values>10**(-.5)*1.02)&(values<10**1.6*.98)).astype(float)
        gram=np.einsum('rpi,rpj,rp->rij',design,design,weight);rhs=np.einsum('rpi,rp,rp->ri',design,values,weight)
        coefficients=nuisance_coefficients(gram,rhs)
        expected=np.einsum('rpi,ri->rp',design,coefficients)
        mse=np.mean(((expected-values)*weight)**2,axis=1)
        records.append((image['array'],times,values,design,weight,coefficients,mse))
    for index,candidate in enumerate(output):
        control.check();observations={};rows={}
        for array,times,values,design,weight,coefficients,mse in records:
            keep=[i for i in range(design.shape[2]) if i!=2+index]
            reduced=design[:,:,keep]
            gram=np.einsum('rpi,rpj,rp->rij',reduced,reduced,weight);rhs=np.einsum('rpi,rp,rp->ri',reduced,values,weight)
            without=np.einsum('rpi,ri->rp',reduced,nuisance_coefficients(gram,rhs))
            baseline=np.mean(((without-values)*weight)**2,axis=1)
            gain=(baseline-mse)/np.maximum(baseline,1e-6);amplitude=coefficients[:,2+index]
            for t,a,g in zip(times,amplitude,gain):
                t=round(float(t),6)
                rows.setdefault(t,{})[array]={'amplitude':float(a),'relative_gain':float(g)}
                if a>=.5 and g>=.1:observations.setdefault(t,set()).add(array)
        supported=sorted(t for t,arrays in observations.items() if len(arrays)==2)
        all_times=sorted(rows);step=float(np.median(np.diff(all_times))) if len(all_times)>1 else 1.
        runs=[]
        for t in supported:
            if not runs or t-runs[-1][-1]>step*1.5:runs.append([])
            runs[-1].append(t)
        old=[candidate['start_t'],candidate['end_t']]
        runs=[r for r in runs if len(r)>=5 and r[0]<=old[1] and r[-1]>=old[0]]
        run=max(runs,key=lambda r:(min(r[-1],old[1])-max(r[0],old[0]),len(r))) if runs else None
        if run:
            candidate['start_t']=min(old[0],run[0]);candidate['end_t']=max(old[1],run[-1])
        changed=[candidate['start_t'],candidate['end_t']]!=old
        if changed:candidate['visibility_extension_source']='dual_raster_leave_one_identity_out'
        report['updates'].append({'hypothesis_id':candidate['id'],'before':old,
                                  'after':[candidate['start_t'],candidate['end_t']],'extended':changed,
                                  'dual_supported_run':run or [],'rows':[{'t':t,**v} for t,v in sorted(rows.items())]})
    return output,{**report,'status':'completed','window_extension_is_not_position_truth':True}
