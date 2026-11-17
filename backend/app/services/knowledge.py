"""Reviewed, versioned local rules. Never ingest target truth or arbitrary documents."""
import sqlite3
from contextlib import closing

RULES = (
    ('clutter-review-v1','observation','btr-v1','杂波 排除 交叉 折返',
     '亮带直线、较弱、局部跳动或折返不能单独证明杂波；候选片段可能串线。方位角不是仰角。不确定成因保留ambiguous，不得凭模型推测固定障碍、多径或海底地貌而排除跨时刻候选。'),
    ('count-v1','association','btr-v1','数量 清单 漏检 镜像',
     '先列出有跨时刻图像证据的目标清单，再做定位；无法配对或定位的目标不能从清单消失。两阵、断段、镜像均不可重复计数；不能按预设目标数凑答案。'),
    ('tracking-v1','tracking','btr-v1','连续 速度 加速度 缺测',
     '连续估计只能调整已有观测时刻的位置，不能修改原方位或增加时间点。运动约束是工程先验，缺测、拒绝或明显跳变必须断段。'),
    ('reprojection-v1','reprojection','btr-v1','重投影 误差 镜像',
     '空间结果投回两阵后需核对锁定候选和像素误差；一致性通过不是独立真值验证，镜像和错误关联仍可能通过。'),
    ('calibration-v1','calibration','btr-v1','绘图区 标定 时间 色条',
     '角度按有效绘图区左右边界线性映射，时间方向和范围来自platform.json；排除黑边、色条。'),
    ('folding-v1','association','btr-v1','折叠 方位 镜像 左右舷',
     '0～180度折叠方位丢失左右舷符号；可行镜像必须保留，不能凭参考图选择。'),
    ('pairing-v1','association','btr-v1','双阵 配对 交叉 连续',
     '只匹配同平台、同时间、同目标的hull/tow候选点；不能因亮度相似就认定同目标。交叉处保持多假设或断开。'),
    ('parallax-v1','diagnosis','btr-v1','视差 基线 远距离 不稳定',
     '短基线与远距离目标可能视差很小。角度误差会放大为距离误差；不存在稳定交点时应报告无法定位，不能修改观测角度。'),
    ('evidence-v1','observation','btr-v1','候选 像素 亮带 杂波',
     '候选峰只是图像证据，不等于目标。大模型只能选择给定候选编号，不能新增角度；误检、漏检必须保留告警。'),
    ('coverage-v1','diagnosis','btr-v1','覆盖率 两点 完整',
     '两个时刻只能构成局部候选线段，不能声称完整轨迹。不得跨缺测或被拒绝的时刻补线。'),
)


def retrieve_rules(stage,version='btr-v1',limit=4):
    # Deterministic structured retrieval for this small rule base, not fake vector RAG.
    # In-memory SQLite avoids mutable shared indexes and unreviewed runtime ingestion.
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('CREATE TABLE rules(id TEXT PRIMARY KEY,stage TEXT,version TEXT,keywords TEXT,body TEXT)')
        db.executemany('INSERT INTO rules VALUES(?,?,?,?,?)',RULES)
        rows=db.execute('SELECT id,stage,version,body FROM rules WHERE version=? AND stage IN (?,?) ORDER BY id LIMIT ?',
                        (version,stage,'calibration',min(limit,8))).fetchall()
    return [{'id':r[0],'stage':r[1],'version':r[2],'text':r[3],
             'source':'builtin-project-rules','reviewed':True,'review_type':'code-reviewed-not-domain-expert-validated'} for r in rows]
