"""Image-likelihood refinement of fixed identities; never receives target labels.

Row amplitudes and background are nuisance variables. Fitting all Gaussian
components together prevents an overlapping neighbour pulling a centroid.
"""
import copy
import math
from time import monotonic

import numpy as np
from scipy.optimize import least_squares

from backend.app.services.localization.batch_inverse import evaluate_path,predicted_bearings,reference_for
from backend.app.services.localization.forward_observation import array_location
from backend.app.services.localization.motion_families import family_seeds
from backend.app.services.localization.observability import range_sensitivity


class RefinementBudgetReached(Exception):
    """Optional refinement stopped; completed candidates remain usable."""


def nuisance_coefficients(gram,rhs):
    """Bounded active-set nuisance fit; the periodic texture coefficient remains signed."""
    n=rhs.shape[1];matrix=gram+np.eye(n)*1e-5
    free=np.ones(rhs.shape,dtype=bool);constrained=np.ones(n,dtype=bool);constrained[1]=False
    coefficients=np.zeros_like(rhs)
    for _ in range(n*2+2):
        reduced=matrix*free[:,:,None]*free[:,None,:]+np.eye(n)[None,:,:]*(~free)[:,:,None]
        coefficients=np.linalg.solve(reduced,(rhs*free)[...,None])[...,0]
        negative=(coefficients < -1e-9)&free&constrained[None,:]
        if np.any(negative):
            free[negative]=False
            continue
        gradient=np.einsum('rij,rj->ri',matrix,coefficients)-rhs
        eligible=(~free)&constrained[None,:]&(gradient < -1e-8)
        if not np.any(eligible):break
        choices=np.argmin(np.where(eligible,gradient,np.inf),axis=1)
        rows=np.flatnonzero(eligible.any(axis=1));free[rows,choices[rows]]=True
    coefficients[:,constrained]=np.maximum(0,coefficients[:,constrained])
    return coefficients


def refine_profiles(selection,evidence,context,control,rounds=2,profile_rows=257,*,phase_seconds=60.,reserve_seconds=15.,progress=None):
    if type(rounds) is not int or not 1<=rounds<=2 or type(profile_rows) is not int or not 33<=profile_rows<=513:
        raise ValueError('图像联合拟合预算无效')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (phase_seconds,reserve_seconds)):
        raise ValueError('图像细化阶段预算必须是非负有限数值')
    control.check()
    deadline=monotonic()+min(phase_seconds,max(0.,control.remaining-reserve_seconds))
    def check():
        control.check()  # User cancellation must never be swallowed as a fallback.
        if monotonic()>=deadline:raise RefinementBudgetReached()
    def notify(iteration,index,phase):
        if progress:progress({'iteration':iteration+1,'hypothesis_id':selected[index]['id'],
                              'phase':phase,'remaining_s':max(0,round(deadline-monotonic(),1))})
    selected=copy.deepcopy(selection['selected']);report={'method':'joint_image_variable_projection_v2','updates':[],'rounds':rounds,
        'profile_rows_budget':profile_rows,'uses_reference_labels':False,'independent_accuracy_verified':False,
        'validation':'interleaved_raster_rows_not_independent_position_truth','status':'completed',
        'phase_seconds_budget':phase_seconds,'reserved_output_seconds':reserve_seconds}
    if not selected:return selection,report
    # This first implementation retains the explicit collinear mirror family.
    platform=context['platforms'][0];reference=reference_for(context)
    if any(abs(platform[a+'_m'][1])>1e-8 for a in ('hull','tow')):
        return selection,report|{'status':'unsupported_noncollinear_geometry'}
    images=sorted(evidence['images'],key=lambda i:i['array'])
    lookup={p['id']:(image['array'],p) for image in images for p in image['candidates']}
    report['observation_window_updates']=[]
    for candidate in selected:
        support={}
        for cid in candidate['supported_measurement_ids']:
            if cid in lookup:
                array,p=lookup[cid];support.setdefault(p['t'],set()).add(array)
        paired=sorted(t for t,arrays in support.items() if len(arrays)==2)
        old=[candidate['start_t'],candidate['end_t']];window=old.copy()
        # A one-array peak after an X crossing may belong to another target.
        # Only modest extensions supported by BOTH arrays can widen a window.
        extension=min(30.,context['T_MAX']*.05)
        if paired and 0<old[0]-paired[0]<=extension:window[0]=paired[0]
        if paired and 0<paired[-1]-old[1]<=extension:window[1]=paired[-1]
        candidate['start_t'],candidate['end_t']=window
        report['observation_window_updates'].append({'hypothesis_id':candidate['id'],'before':old,'after':window})
    data=[];widths=[];models=[c['variants'][0] for c in selected]
    for image in images:
        times=np.array([p['t'] for p in image['profiles']]);angles=image['_angles'][::2]
        profiles=image['profiles']
        if '_raster_relative' in image:
            raster=image['_raster_relative'];valid=image['_raster_valid'];profiles=[];dense=[]
            # Additional actual raster rows, not invented/interpolated angles.
            for row,fraction in enumerate(np.linspace(.02,.98,min(profile_rows,len(raster)))):
                y=round((1-fraction)*(len(raster)-1) if context.get('image_time_axis','bottom_to_top')=='bottom_to_top' else fraction*(len(raster)-1))
                lo,hi=max(0,y-1),min(len(raster),y+2)
                values=np.ma.median(np.ma.array(raster[lo:hi],mask=~valid[lo:hi]),axis=0).filled(np.nan)
                values=np.where(np.isfinite(values),values,np.nanmedian(values))
                dense.append(values[::2]);profiles.append({'sample_index':row,'t':float(fraction*context['T_MAX'])})
            times=np.array([p['t'] for p in profiles]);values=np.array(dense)
        else:values=image['_profile_values'][:,::2].astype(float)
        centers=np.array([array_location(platform,platform[image['array']+'_m'],float(t),reference) for t in times])
        scale=np.maximum(1,np.percentile(values,95,axis=1))
        # Approximate censoring: saturated pixels are checked one-sided below.
        data.append((profiles,times,angles,centers,values,scale))
        lookup={p['id']:p for p in image['candidates']}
        widths.append([float(np.median([lookup[i]['sigma_deg'] for i in c['supported_measurement_ids'] if i in lookup]))
                       if any(i in lookup for i in c['supported_measurement_ids']) else 2. for c in selected])
    widths=np.clip(np.array(widths),.3,10.)
    origin=np.array(reference.to_xy(platform['start_ll']))/1000
    def predictions(model,times,centers):
        xy=evaluate_path(model['parameters'],times,model['model'],model['t0'],model['span'])
        return predicted_bearings(xy,centers,platform['heading_deg'],reference)
    cache={}
    def residual_for(index,parameters,mask_kind,base_model=None):
        check();model={**(base_model or models[index]),'parameters':parameters[:-2]};residuals=[]
        for array,(profiles,times,angles,centers,values,scale) in enumerate(data):
            key=(index,array)
            if key not in cache:
                templates=[]
                for j,other in enumerate(models):
                    bearing=predictions(other,times,centers)
                    # Visibility is an unknown nonnegative row amplitude. A
                    # ridge endpoint is not proof that its image energy vanishes.
                    templates.append(np.exp(-.5*((angles[None,:]-bearing[:,None])/widths[array,j])**2))
                texture=np.broadcast_to(np.cos(2*np.pi*angles/120.),values.shape)
                design=np.stack([np.ones_like(values),texture,*templates],axis=2)
                # Clipped colour levels are inequalities, not exact intensities.
                nuisance_weight=((values>10**(-.5)*1.02)&(values<10**1.6*.98)).astype(float)
                gram=np.einsum('rpi,rpj,rp->rij',design,design,nuisance_weight);rhs=np.einsum('rpi,rp,rp->ri',design,values,nuisance_weight)
                initial_coefficients=nuisance_coefficients(gram,rhs)
                cache[key]=(design,gram,rhs,initial_coefficients[:,2:]>.5,nuisance_weight)
            design,base_gram,base_rhs,visibility,nuisance_weight=cache[key]
            bearing=predictions(model,times,centers);sigma=math.exp(parameters[-2+array])
            template=np.exp(-.5*((angles[None,:]-bearing[:,None])/sigma)**2)
            column=2+index;design[:,:,column]=template
            # Refit constrained nuisance amplitudes, not an unconstrained solve followed by clipping.
            gram=base_gram.copy();rhs=base_rhs.copy()
            products=np.einsum('rp,rpi,rp->ri',template,design,nuisance_weight)
            gram[:,:,column]=products;gram[:,column,:]=products
            rhs[:,column]=np.einsum('rp,rp,rp->r',template,values,nuisance_weight)
            coefficients=nuisance_coefficients(gram,rhs)
            expected=np.einsum('rpi,ri->rp',design,coefficients)
            delta=(expected-values)/scale[:,None]
            delta[values>=10**1.6*.98]=np.minimum(delta[values>=10**1.6*.98],0)
            delta[values<=10**(-.5)*1.02]=np.maximum(delta[values<=10**(-.5)*1.02],0)
            mask=np.array([p['sample_index']%5!=0 for p in profiles])
            if mask_kind=='validation':mask=~mask
            if mask_kind=='all':mask[:]=True
            # Weight only the current target's observed window, not background far away.
            b=predictions(models[index],times,centers)
            region=np.abs(angles[None,:]-b[:,None])<=max(6.,widths[array,index]*3)
            region&=(times>=selected[index]['start_t'])[:,None]&(times<=selected[index]['end_t'])[:,None]
            # Unresolved mixtures can explain image energy but must not drive
            # independent distance estimates for either owner of an X crossing.
            clear=np.ones(len(times),dtype=bool)
            for j,other in enumerate(models):
                if j==index:continue
                active=visibility[:,j]
                separation=abs(b-predictions(other,times,centers))
                resolution=min(widths[array,index],widths[array,j])
                clear&=~active|(separation>resolution)
            region&=clear[:,None]
            residuals.append(delta[region&mask[:,None]])
        return np.concatenate(residuals)
    def optimize():
        for iteration in range(rounds):
            for index,candidate in enumerate(selected):
                cache.clear();check();model=models[index];initial=np.r_[model['parameters'],np.log(widths[:,index])]
                notify(iteration,index,'开始图像细化')
                lo=np.full(len(initial),-100.);hi=np.full(len(initial),100.)
                lo[:2]=origin-100;hi[:2]=origin+100
                if model['model']=='harmonic':lo[-3]=.25;hi[-3]=4.
                if model['model']=='waypoint':lo[-3]=-.45;hi[-3]=.45
                lo[-2:]=np.log(.3);hi[-2:]=np.log(10.)
                initial=np.clip(initial,lo+1e-7,hi-1e-7)
                active=np.arange(len(initial))
                family=model.get('parameter_family')
                if model['model']=='cv' and family in ('constant_latitude','constant_longitude'):
                    zero=3 if family=='constant_latitude' else 2
                    if abs(initial[zero])<1e-8:active=np.array([k for k in active if k!=zero])
                if model['model']=='harmonic' and family in ('latitude_harmonic','longitude_harmonic'):
                    excluded=(4,6) if family=='latitude_harmonic' else (5,7)
                    # Preserve only an actually axis-constrained solution, not its
                    # rotated mirror which uses the full harmonic representation.
                    if all(abs(initial[k])<1e-8 for k in excluded):active=np.array([k for k in active if k not in excluded])
                training=residual_for(index,initial,'train');validation=residual_for(index,initial,'validation')
                if len(training)<50 or len(validation)<20:
                    report['updates'].append({'round':iteration+1,'hypothesis_id':candidate['id'],'accepted':False,
                        'reason':'insufficient_resolved_pixels','training_pixels':len(training),'validation_pixels':len(validation)})
                    continue
                before=float(np.mean(validation**2))
                # Penalize model complexity by actual time rows, not thousands of
                # correlated angular pixels. This is a ranking proxy, not evidence
                # of a uniquely known motion family or side.
                effective_rows=max(10,sum(np.count_nonzero((times>=candidate['start_t'])&(times<=candidate['end_t'])) for _,times,_,_,_,_ in data))
                def score(mse,count):return effective_rows*math.log(max(1e-12,mse))+count*math.log(effective_rows)
                before_score=score(before,len(active));families=[(family or model['model'],initial,active,model)]
                if iteration==0:
                    # Compare simpler motion and both independent-axis harmonic
                    # families AFTER identity selection. Initial centroid BIC must
                    # not permanently lock a high-dimensional, wrong-heading fit.
                    seeds=family_seeds(model,candidate['variants'],candidate,images,context,check)
                    chosen={}
                    for seed,indices in seeds:
                        name=seed['parameter_family']
                        template=np.r_[seed['parameters'],np.log(widths[:,index])]
                        indices=np.r_[indices,len(template)-2,len(template)-1]
                        residual=residual_for(index,template,'train',seed)
                        if len(residual)<50:continue
                        value=float(np.mean(residual**2))
                        if name not in chosen or value<chosen[name][0]:chosen[name]=(value,template,indices,seed)
                    families.extend((name,item[1],item[2],item[3]) for name,item in chosen.items()
                                    if not (name==(family or model['model']) and item[3]['model']==model['model']))
                proposals=[];tests=[]
                for name,template,indices,family_model in families:
                    lower=np.full(len(template),-100.);upper=np.full(len(template),100.)
                    lower[:2]=origin-100;upper[:2]=origin+100
                    if family_model['model']=='harmonic':lower[-3]=.25;upper[-3]=4.
                    if family_model['model']=='waypoint':lower[-3]=-.45;upper[-3]=.45
                    lower[-2:]=np.log(.3);upper[-2:]=np.log(10.)
                    template=np.clip(template,lower+1e-7,upper-1e-7)
                    def expand(values):
                        full=template.copy();full[indices]=values
                        return full
                    fit=least_squares(lambda p:residual_for(index,expand(p),'train',family_model),template[indices],bounds=(lower[indices],upper[indices]),loss='soft_l1',
                                      f_scale=.1,max_nfev=45 if name==(family or model['model']) else 25,x_scale='jac')
                    parameters=expand(fit.x);after=float(np.mean(residual_for(index,parameters,'validation',family_model)**2))
                    value=score(after,len(indices));grid=np.linspace(candidate['start_t'],candidate['end_t'],30)
                    xy=evaluate_path(parameters[:-2],grid,family_model['model'],family_model['t0'],family_model['span'])
                    speed=np.linalg.norm(np.diff(xy,axis=0),axis=1)/np.diff(grid)
                    eligible=bool(fit.success and speed.max()<50 and np.linalg.norm(xy-origin*1000,axis=1).max()<100000)
                    tests.append({'family':name,'validation_mse':after,'selection_score':value,'eligible':eligible,'model':family_model['model']})
                    notify(iteration,index,'已比较运动族 '+name)
                    if eligible:proposals.append((value,after,parameters,name,len(indices),family_model))
                best=min(proposals,key=lambda p:p[0]) if proposals else None
                accepted=bool(best and best[0]<before_score-1e-6)
                report['updates'].append({'round':iteration+1,'hypothesis_id':candidate['id'],'accepted':accepted,
                    'validation_intensity_mse_before':before,'validation_intensity_mse_after':best[1] if best else None,
                    'selection_score_before':before_score,'selection_score_after':best[0] if best else None,
                    'motion_family_tests':tests,'optimizer_success':best is not None})
                if accepted:
                    value,after,parameters,name,count,family_model=best
                    competing=sorted(item[0] for item in proposals)
                    models[index]={**family_model,'parameters':parameters[:-2].tolist(),'profile_refined':True,
                        'parameter_family':name,'effective_parameters':count-2,
                        'image_validation_mse':after,'image_selection_score':value,'validation_is_truth':False,
                        'family_competition_margin':competing[1]-competing[0] if len(competing)>1 else model.get('family_competition_margin'),
                        'family_prior':'engineering_motion_candidates_not_known_target_headings'}
                    widths[:,index]=np.exp(parameters[-2:])
    try:
        optimize()
    except RefinementBudgetReached:
        report['status']='budget_limited'
        report['partial_optimization']=True
        report['reason']='图像细化达到阶段预算；保留已完成并验证的更新，其余使用已有候选，预留质检绘图时间'
    control.check()
    vector=np.array([math.sin(math.radians(platform['heading_deg'])),math.cos(math.radians(platform['heading_deg']))])
    reflection=2*np.outer(vector,vector)-np.eye(2)
    for i,candidate in enumerate(selected):
        model=models[i];variants=copy.deepcopy(candidate['variants'])
        if model.get('profile_refined'):
            variants=[model]
        if model.get('profile_refined') and len(candidate['variants'])>1:
            p=np.array(model['parameters']);tail=p[-1:] if model['model'] in ('harmonic','waypoint') else np.zeros(0)
            coefficients=p[:-1].reshape(-1,2) if len(tail) else p.reshape(-1,2)
            reflected=coefficients@reflection.T;reflected[0]=origin+reflection@(coefficients[0]-origin)
            mirror={**model,'parameters':np.r_[reflected.ravel(),tail].tolist(),'mirror_from_forward_symmetry':True}
            differences=[]
            for _,times,_,centers,_,_ in data:
                differences.extend(abs(predictions(mirror,times,centers)-predictions(model,times,centers)))
            if max(differences)<.1:variants.append(mirror)
            else:variants.extend(copy.deepcopy(candidate['variants'][1:]))
        for variant in variants:
            errors=[];validation=[]
            for image in images:
                measurements=[p for p in image['candidates'] if p['id'] in candidate['supported_measurement_ids']]
                if not measurements:continue
                times=np.array([p['t'] for p in measurements])
                centers=np.array([array_location(platform,platform[image['array']+'_m'],float(t),reference) for t in times])
                differences=predictions(variant,times,centers)-np.array([p['bearing_deg'] for p in measurements])
                errors.extend(differences);validation.extend(differences[[p['sample_index']%5==0 for p in measurements]])
            variant['rmse_deg']=float(np.sqrt(np.mean(np.array(errors)**2)))
            variant['validation_rmse_deg']=float(np.sqrt(np.mean(np.array(validation or errors)**2)))
            variant['condition_proxy_source']='initial_centroid_fit_not_image_posterior'
            variant['score_source']='held_out_image_and_engineering_complexity' if variant.get('profile_refined') else 'initial_centroid_model_selection'
            variant['range_sensitivity']=range_sensitivity(variant,candidate,evidence,context)
            variant['position_information_weak']=bool(variant.get('position_information_weak') or variant['range_sensitivity']['range_sensitive'])
        candidate['variants']=variants
        candidate['profile_widths_deg']={im['array']:float(widths[a,i]) for a,im in enumerate(images)}
    return {**selection,'selected':selected},report
