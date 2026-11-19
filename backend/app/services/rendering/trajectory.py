"""Trajectory rendering and the legacy rendering entry point."""
import json
import math
from pathlib import Path
import re
import threading
from backend.app.services.inputs.track_validation import parse_tracks

PLOT_LOCK=threading.Lock()


def render_tracks(answer,directory,context=None):
    if context and context.get('require_spatial'):
        from backend.app.services.localization.spatial_tracks import solve_spatial
        text=re.sub(r'^```(?:json)?\s*|\s*```$','',answer.strip(),flags=re.I)
        document=json.loads(text)
        if not isinstance(document,dict):raise ValueError('模型未返回双阵观测JSON对象')
        doc=solve_spatial(document,context)
    else:
        doc=parse_tracks(answer,context)
    return render_document(doc,directory,context)


def render_document(doc,directory,context=None,*,filename='estimated_track.png',persist=True):
    """Draw an already validated local result; never re-solve its observations."""
    if context and context.get('platforms') and doc['coordinate_system']!='bearing_time':
        plat=context['platforms'][0]
        doc['coordinate_reference']={'platform_id':plat.get('name','A'),
            'origin_lat':plat['start_ll'][0],'origin_lon':plat['start_ll'][1],
            'earth_radius_m':context.get('EARTH_R',6371000.0),'projection_lat':context.get('REF_LAT',plat['start_ll'][0])}
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    frame=doc['coordinate_system']
    colors=['#ffa600','#f000d0','#00d9e8','#a2ff00','#6aa5ff','#ff6d7c']
    spatial=doc.get('plot_kind')=='spatial_candidates'
    identity_colors={identity:colors[i%len(colors)] for i,identity in enumerate(dict.fromkeys(t['id'] for t in doc['tracks']))}
    seen_labels=set()
    if spatial:
        doc['plot_summary']={'identity_hypotheses':len(identity_colors),'candidate_segments':len(doc['tracks']),
                             'target_count_confirmed':False,'colors_grouped_by_identity':True,
                             'mirror_styles_not_unique_solutions':True}
    with PLOT_LOCK:
        fig=Figure(figsize=(11,6.5),dpi=150,facecolor='#050810');FigureCanvasAgg(fig)
        ax=fig.add_subplot(111);ax.set_facecolor('#0a0f1f')
        for i,track in enumerate(doc['tracks']):
            pts=track['points'];fallback=identity_colors[track['id']] if spatial else colors[i%len(colors)]
            color=context.get('target_colors',{}).get(track['id'],fallback) if context else fallback
            # Display labels are generated locally; never interpret model LaTeX.
            endpoints=doc.get('plot_kind')=='endpoints'
            linestyle='None' if endpoints else '-'
            label=f'Target {i+1} endpoints' if endpoints else f'Track {i+1}'
            if spatial:
                side=tuple(track.get('handedness',()))
                if 'mirror_variant' in track:
                    alternative=track['mirror_variant'];side=('alternative',alternative)
                    linestyle=['-','--',':','-.'][alternative%4]
                    sides=f'alternative {alternative+1}'
                else:
                    linestyle={(1,1):'-',(-1,-1):'--',(1,-1):':',(-1,1):'-.'}.get(side,'-')
                    sides='side '+('/'.join(f'{sign:+d}' for sign in side) if side else 'unspecified')
                key=(track['id'],side)
                label='_nolegend_' if key in seen_labels else f"{track['id']} | {sides} (candidate)"
                seen_labels.add(key)
            ax.plot([p['x'] for p in pts],[p['y'] for p in pts],color=color,lw=1.4,
                    linestyle=linestyle,marker='o' if endpoints else None,label=label)
            ax.plot(pts[0]['x'],pts[0]['y'],'o',color=color,ms=4)
            if endpoints:ax.plot(pts[-1]['x'],pts[-1]['y'],'x',color=color,ms=7)
        if frame!='bearing_time' and context.get('platforms'):
            start=context['platforms'][0]['start_ll']
            x,y=(start[1],start[0]) if frame=='lon_lat' else (0,0)
            ax.plot(x,y,'s',color='#ffd400',label='Platform initial position',ms=6)
            if frame=='lon_lat':
                for plat in context['platforms'][1:]:
                    ax.plot(plat['start_ll'][1],plat['start_ll'][0],'s',color='#ffd400',ms=6)
            if frame=='local_xy_m':ax.set_aspect('equal',adjustable='datalim')
            elif abs(start[0])<89:ax.set_aspect(1/math.cos(math.radians(start[0])),adjustable='datalim')
        elif frame=='local_xy_m':
            ax.set_aspect('equal',adjustable='datalim')
        elif frame=='lon_lat':
            latitude=doc['tracks'][0]['points'][0]['y']
            if abs(latitude)<89:ax.set_aspect(1/math.cos(math.radians(latitude)),adjustable='datalim')
        labels={'local_xy_m':('East (m), relative to platform initial position','North (m)'),
                'lon_lat':('Longitude (deg)','Latitude (deg)'),
                'bearing_time':('Relative folded bearing (deg)','Time (s)')}
        if doc.get('input_mode')=='data':labels['local_xy_m']=('East (m), supplied local coordinates','North (m)')
        ax.set_xlabel(labels[frame][0],color='#dce3ee');ax.set_ylabel(labels[frame][1],color='#dce3ee')
        ax.tick_params(colors='#a6b4c8');ax.grid(color='#566070',alpha=.3,linestyle=':')
        if frame=='bearing_time':ax.set_xlim(0,180)
        title='USER DATA PLOT - NOT SONAR INFERENCE' if doc.get('input_mode')=='data' else 'MODEL ESTIMATE - NOT GROUND TRUTH'
        if doc.get('plot_kind')=='endpoints':title='REFERENCE ENDPOINTS ONLY - NOT COMPLETE TRACKS'
        if doc.get('plot_kind')=='meta_reconstruction':title='META RECONSTRUCTION - FULL SIMULATION TRACKS'
        if doc.get('plot_kind')=='spatial_candidates':title='DUAL-ARRAY SPATIAL CANDIDATES - MIRROR AMBIGUITY'
        if doc.get('display_view')=='primary_motion_hypotheses':title='PRIMARY MOTION HYPOTHESES - SIDE NOT VERIFIED'
        if spatial:title+=f"\n{len(identity_colors)} identity hypotheses / {len(doc['tracks'])} fragments - NOT confirmed targets"
        ax.set_title(title+(' / BEARING-TIME ONLY' if frame=='bearing_time' else ''),color='#dce3ee',fontsize=11)
        ax.legend(facecolor='#101828',labelcolor='#dce3ee',fontsize=8)
        directory=Path(directory)
        if filename not in ('estimated_track.png','mirror_candidates.png'):raise ValueError('轨迹视图文件名无效')
        fig.savefig(directory/filename,bbox_inches='tight',facecolor=fig.get_facecolor())
        if persist:(directory/'tracks.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
        fig.clear()
    return doc
