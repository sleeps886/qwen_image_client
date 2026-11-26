"""Describe valid observations without rejecting sparse/asynchronous input."""
def observation_diagnostics(points):
    groups={}
    for point in points:
        groups.setdefault((point['platform_id'],point['t']),set()).add(point['array'])
    dual=sum({'hull','tow'}<=arrays for arrays in groups.values())
    return {'policy':'accept-sparse-v1','scope':'current_observation_pool',
            'point_count':len(points),'group_count':len(groups),
            'hull_point_count':sum(p['array']=='hull' for p in points),
            'tow_point_count':sum(p['array']=='tow' for p in points),
            'strict_dual_group_count':dual,'single_array_group_count':len(groups)-dual,
            'synchronization_rule':'same-platform-exact-time','target_pairing_confirmed':False,
            'note':'单点、缺阵、不同时间均可分析；本轮计数不代替累计历史证据，不证明同目标配对或空间可定位。'}
