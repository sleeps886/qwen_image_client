"""Post-inference report only; never creates a run or calls a model."""
import argparse
import csv
import html
import json
import math
from pathlib import Path
import zipfile

from backend.app.services.localization.spatial_tracks import array_position
from backend.tools.check_supplied_ten_samples import TEMP


def aligned_plot(case,names):
    public=json.loads((case/'public-run.json').read_text(encoding='utf-8'));view=public['working_view'];doc=view['spatial_preview'];origin=doc['origin_lat_lon']
    truth={}
    for i,name in enumerate(names,1):
        with zipfile.ZipFile(TEMP/name) as archive:reference=json.loads(archive.read('evaluation/reference.json'))
        truth[f'I{i:02d}']={p['t']:(6371000*math.cos(math.radians(origin[0]))*math.radians(p['longitude']-origin[1]),
            6371000*math.radians(p['latitude']-origin[0])) for p in reference['points']}
    branches=doc['model_branch_choices'];paths={};axes_nav=public['steps'][-1]['context']['platform_configuration']['platforms']
    for track in doc['tracks']:
        branch=''.join('p' if h==1 else 'm' for h in track['handedness'])
        for p in track['points']:
            if branches.get(p['unit_id'])!=branch:continue
            cid=p['candidate_ids'][0];target=view['membership'][cid].get('target_id')
            if not target:continue
            nav=next(n for n in axes_nav if n['name']==track['platform_id'])
            h=array_position(nav,nav['hull_m'],p['t'],origin);t=array_position(nav,nav['tow_m'],p['t'],origin)
            dx,dy=t[0]-h[0],t[1]-h[1];span=math.hypot(dx,dy);dx/=span;dy/=span
            qx,qy=p['x']-h[0],p['y']-h[1];along=qx*dx+qy*dy;mirror=(h[0]+2*along*dx-qx,h[1]+2*along*dy-qy)
            paths.setdefault(target,[]).append({'t':p['t'],'point':(p['x'],p['y']),'reflected':mirror,'reference':truth[cid.split('-')[0]][p['t']]})
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axs=plt.subplots(1,3,figsize=(16,4.8),facecolor='#0b1020');colors=['#50c6ad','#e6b566','#b59aef','#f28484']
    for ax in axs:
        ax.set_facecolor('#0b1020');ax.tick_params(colors='#bed0df');ax.grid(alpha=.15);ax.set_aspect('equal',adjustable='datalim')
        ax.set_xlabel('East (m)',color='white');ax.set_ylabel('North (m)',color='white')
    for (source,path),color in zip(truth.items(),colors):
        ps=list(path.values());axs[0].plot([p[0] for p in ps],[p[1] for p in ps],color=color,label=source)
        axs[2].plot([p[0] for p in ps],[p[1] for p in ps],color='#c3ced6',alpha=.4,linestyle='--',lw=1)
    for (target,path),color in zip(paths.items(),colors):
        path.sort(key=lambda p:p['t']);cost=[sum(math.dist(p[k],p['reference'])**2 for p in path) for k in ('point','reflected')]
        chosen='point' if cost[0]<=cost[1] else 'reflected'
        for ax,key in ((axs[1],'point'),(axs[2],chosen)):
            ax.plot([p[key][0] for p in path],[p[key][1] for p in path],color=color,marker='.',markersize=3,label=target)
    for ax,title in zip(axs,('Reference (evaluation ONLY)','Actual model branch (may be mirror)','Whole-track mirror alignment (evaluation ONLY)')):
        ax.set_title(title,color='white',fontsize=10)
        if ax.get_legend_handles_labels()[0]:ax.legend(fontsize=7)
    fig.tight_layout();fig.savefig(case/'shape-evaluation.png',dpi=120);plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);args=p.parse_args();root=args.root
    reports=json.loads((root/'reports.json').read_text(encoding='utf-8'));inventory=json.loads((root/'inventory.json').read_text(encoding='utf-8'))
    total={'samples':len(reports),'all_imported_observations':sum(r['raw_observations'] for r in inventory),
        'all_paired_groups':sum(r['available_paired_groups'] for r in inventory),'model_sampled_groups':sum(r['sampled_groups'] for r in reports),
        'model_processed_groups':sum(r['processed_groups'] for r in reports),'selected_positions':sum(r['selected_positions'] for r in reports),
        'unassigned_groups':sum(r['unassigned_groups'] for r in reports),'real_model_requests':sum(r['real_requests'] for r in reports),
        'cross_source_edges':sum(r['cross_source_edges'] for r in reports),'status_counts':{},'reference_used_for_inference':False,'production_sessions_modified':False,
        'scope':'Full import/geometry diagnostics for 4623 pairs; true model inference of 50 groups/sample, not full inference of all pairs.'}
    for r in reports:total['status_counts'][r['status']]=total['status_counts'].get(r['status'],0)+1
    (root/'summary.json').write_text(json.dumps(total,ensure_ascii=False,indent=2),encoding='utf-8')
    header=['样本','有交点/50','模型选位置/50','目标假设数','未归属','镜像对齐RMSE米（仅评估）','候选最高覆盖','真实调用']
    rows=[];cards=[]
    for meta,r in zip(inventory,reports):
        case=root/f'sample-{r["sample"]:02d}';aligned_plot(case,meta['archives'])
        values=[r['sample'],r['sample_geometry_groups'],r['selected_positions'],r['target_hypotheses'],r['unassigned_groups'],
            round(r['mirror_aligned_rmse_m_evaluation_only'],2) if r['mirror_aligned_rmse_m_evaluation_only'] is not None else '-',r['numeric_candidate_max_coverage'],r['real_requests']]
        rows.append(values)
        image=f'sample-{r["sample"]:02d}/shape-evaluation.png'
        warning='覆盖不足' if r['selected_positions']<r['sample_geometry_groups'] else '几何缺失' if r['selected_positions']<50 else '本次抽样完整'
        cards.append(f'<section id="s{r["sample"]}"><h2>样本{r["sample"]:02d} · {warning}</h2><p>'+html.escape(' / '.join(meta['archives']))+
            f'</p><p>工作位置 {r["selected_positions"]}/50；未归属 {r["unassigned_groups"]}；镜像对齐误差 {values[5]}米（非绝对定位精度）。</p><a href="{image}"><img src="{image}" alt="样本{r["sample"]}：参考、实际分支、仅评估镜像对齐" loading="lazy"></a></section>')
    with (root/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(header);writer.writerows(rows)
    table='<table><thead><tr>'+''.join('<th>'+html.escape(str(c))+'</th>' for c in header)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>'
    doc='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>小怪 · 10份样本真实测试</title><style>
body{margin:0;background:#0b1020;color:#cfdfed;font:15px/1.6 system-ui,sans-serif}main{max-width:1350px;margin:24px auto;padding:20px}h1,h2{color:#78d5bd}table{width:100%;border-collapse:collapse}th,td{padding:9px;border:1px solid #34443d;text-align:center}th{background:#1c2722}section{margin:26px 0;border:1px solid #34443d;padding:16px;border-radius:10px}img{width:100%;height:auto}a{color:#8eb9f6}.warning{border-left:4px solid #e6b566;padding:12px;background:#202b2b}
</style><main><h1>小怪 · 10份样本真实模型测试</h1>'''
    doc+=f'<p>30个ZIP分10份，完整检查9246条观测、4623组双阵；每份均匀抽50组，共500组真实推理，累计{total["real_model_requests"]}次调用。</p>'
    doc+='<p class="warning">执行完成不等于识别质量全部合格。当前选出442/500个工作位置，58组未归属；曲线样本存在候选覆盖瓶颈。整体镜像方向仍有歧义。第三栏借助参考只做评估，不能冒充模型自动选对绝对侧向。</p>'
    doc+=table+''.join(cards)+'</main></html>'
    (root/'index.html').write_text(doc,encoding='utf-8')
    lines=['# 10份样本真实模型测试','',f'总真实请求：{total["real_model_requests"]}；模型抽样500组，选出442个位置，未归属58组。原用户会话未修改。','',
        '> 范围：不是对全部4623组逐一调用模型；全量只做导入和几何检查。模型每份50组跨时间抽样，不构成全时段或在线多批次验收。',
        '> 镜像对齐误差为事后评估，不代表绝对位置已正确。低误差也可能来自丢弃困难点，必须连同覆盖率看。','',
        '| '+' | '.join(header)+' |','|'+'|'.join(['---']*len(header))+'|']
    lines+=['| '+' | '.join(map(str,row))+' |' for row in rows]
    lines+=['','## 已定位限制','',
        '- 样本3、4、5、6都有50个可用几何位置，但候选方案最高仅覆盖34、44、33、40组；不是模型输出乱码，也不是导入少了点。',
        '- 样本3少了一个完整目标假设；样本5、6也存在明显缺点。当前固定35米残差门、全时域线性片段和24张卡的覆盖/互斥组合不适合所有曲线运动。',
        '- 样本8、9、10分别有5、2、2组抽样无可用交点，不能靠补线当成定位成功。',
        '- 样本7的镜像对齐误差仍约64.8米，且与参考最近交点误差一致，需进一步核对球面方位与局部平面求交误差及低视差敏感性；本轮未据此断言唯一根因。',
        '- 已归属442组在本次事后评分中无跨原目标连接，但不能把它当成全部500组识别正确。',
        '- 第一份数据和用户20:01/20:07会话平台参数一致；用户那两份保存队列的spatial_association_contract为空，仍是旧模式。新模式本次第一份可覆盖50组，但侧向仍未唯一确定。','',
        '## 后续优先级','',
        '1. 增加有界滚动窗口曲线候选及不确定度自适应门，保持多个竞争方案，不强制三个目标或放宽门限凑图。',
        '2. 候选缺口应进入局部复核，允许模型用明确组号补充/修订，不让“选方案”协议永久排除已观测的困难点。',
        '3. 补充多批次、逐点、尾部续接与中段插入测试；本轮是每份单批50组的基线。',
        '4. 独立核对远距离、低视差、运动平台的定位前向/逆向一致性；继续区分镜像工作分支与真实侧向。','',
        '## 产物','',
        '- index.html：十份对比图与汇总。','- summary.csv / summary.json：机器可读汇总。','- inventory.json：明确文件分组、原始数与平台参数。',
        '- full-input-geometry.json：全部4623组几何检查。','- sample-XX/public-run.json：实际工作台执行结果、模型原文和证据。',
        '- sample-XX/observations.sqlite3：独立测试快照，未采纳到用户会话。','',
        '本轮只新增测试与分析工具，没有修改生产关联算法、替换用户结果、自动翻镜像或用参考真值为模型指定目标。']
    (root/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(total,ensure_ascii=True));print(str((root/'index.html').resolve()))


if __name__=='__main__':main()
