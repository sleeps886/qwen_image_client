"""Selected dual-array observations, bounded motion families and uncertainty gates."""
import math
import numpy as np
from scipy.optimize import least_squares
from backend.app.services.localization.spatial_tracks import array_position


def evaluate_motion(parameters,family,tau):
    v=np.asarray(parameters);t=np.asarray(tau)[:,None]
    if family=='ca':return v[:2]+t*v[2:4]+.5*t*t*v[4:6]
    if family=='ct':
        w=v[4];a=t*np.sinc(w*t/np.pi);b=.5*w*t*t*np.sinc(.5*w*t/np.pi)**2
        return v[:2]+np.c_[v[2]*a[:,0]-v[3]*b[:,0],v[2]*b[:,0]+v[3]*a[:,0]]
    return v[:2]+t*v[2:4]


def evaluate_candidate(candidate,times):
    return evaluate_motion(candidate['parameters'],candidate['family'],
                           (np.asarray(times)-candidate['t0'])/candidate['span'])*1000.


def temporal_candidates(platform,pairs,families=('cv',)):
    if len(pairs)<4:return []
    times=np.array([p['t'] for p in pairs]);span=times[-1]-times[0]
    if span<=0:return []
    tau=(times-times[0])/span;centers=[];angles=[];sigma=[]
    for p in pairs:
        for array in ('hull','tow'):
            centers.append(array_position(platform,platform[array+'_m'],p['t'],platform['start_ll']))
            angles.append(p[array+'_deg']);sigma.append(p[array+'_error_deg'])
    centers=np.array(centers);angles=np.array(angles);sigma=np.array(sigma);repeated=np.repeat(tau,2)
    results=[]
    for hs,ts in ((1,1),(-1,-1),(1,-1),(-1,1)):
        signs=np.tile([hs,ts],len(pairs));patterns=[signs.copy()]
        flipped=signs.copy()
        # A folded bearing may reverse at boresight/stern. Seed the sign change
        # from observed extrema instead of assuming one side for every time.
        for offset in (0,1):
            values=angles[offset::2];low=int(np.argmin(values));high=int(np.argmax(values))
            turn=low if 0<low<len(values)-1 and values[low]<35 else high if 0<high<len(values)-1 and values[high]>145 else None
            if turn is not None:flipped[2*(turn+1)+offset::2]*=-1
        if not np.array_equal(signs,flipped):patterns.append(flipped)
        seeds=[]
        for pattern in patterns:
            absolute=np.radians(platform['heading_deg']+pattern*angles)
            normals=np.c_[np.cos(absolute),-np.sin(absolute)]
            matrix=np.c_[normals,normals*repeated[:,None]]
            seeds.append(np.linalg.lstsq(matrix,np.sum(normals*centers,axis=1),rcond=None)[0]/1000.)
        cv_seed=None
        for family in families:
            if family not in ('cv','ca','ct') or family!='cv' and len(pairs)<6:continue
            bases=[cv_seed] if cv_seed is not None else seeds
            starts=seeds if family=='cv' else [np.r_[seed,0.,0.] for seed in bases] if family=='ca' else [np.r_[seed,w] for seed in bases for w in (-.8,.8)]
            best=None
            for start in starts:
                def predicted(v):
                    delta=evaluate_motion(v,family,repeated)*1000.-centers
                    bearing=np.degrees(np.arctan2(delta[:,0],delta[:,1]))-platform['heading_deg']
                    return np.abs((bearing+180.)%360.-180.)
                def residual(v):return (predicted(v)-angles)/sigma
                lo=np.full(len(start),-100.);hi=np.full(len(start),100.)
                if family=='ct':lo[-1]=-math.pi;hi[-1]=math.pi
                fit=least_squares(residual,np.clip(start,lo+1e-8,hi-1e-8),bounds=(lo,hi),loss='soft_l1',max_nfev=180)
                errors=predicted(fit.x)-angles
                if np.sqrt(np.mean(errors**2))>1. or max(abs(errors))>2.5:continue
                _,s,vh=np.linalg.svd(fit.jac,full_matrices=False)
                if s[-1]<=max(s[0]*1e-7,1e-8):continue
                covariance=vh.T@np.diag(1./s**2)@vh
                position_sigma=1000.*math.sqrt(float(np.trace(covariance[:2,:2])))
                xy=evaluate_motion(fit.x,family,tau)*1000.;ranges=np.linalg.norm(xy,axis=1)
                speed=float(np.max(np.linalg.norm(np.diff(xy,axis=0),axis=1)/np.diff(times)))
                if min(ranges)<1 or max(ranges)>100000 or speed>150 or position_sigma>min(5000.,float(np.median(ranges))*.5):continue
                signed=(np.degrees(np.arctan2(xy[0,0]-centers[:2,0],xy[0,1]-centers[:2,1]))-platform['heading_deg']+180.)%360.-180.
                if signed[0]*hs<0 or signed[1]*ts<0:continue
                item={'branch':f'temporal_{family}:h{hs:+}t{ts:+}','handedness':[hs,ts],
                      'points':[{'t':float(t),'x':float(p[0]),'y':float(p[1])} for t,p in zip(times,xy)],
                      'method':f'multi_time_{family}_angular_fit','family':family,'parameters':fit.x.tolist(),
                      't0':float(times[0]),'span':float(span),'parameter_count':len(fit.x),
                      'rmse_deg':float(np.sqrt(np.mean(errors**2))),'position_sigma_proxy_m':position_sigma,
                      'speed_mps':speed,'uncertainty_calibrated':False}
                if best is None or item['rmse_deg']<best['rmse_deg']:best=item
            if best:
                results.append(best)
                if family=='cv':cv_seed=np.array(best['parameters'])
    return results
