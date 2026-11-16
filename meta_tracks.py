"""Reconstruct BTR paths from numeric parameters, without eval/exec or file IO."""
import ast
import bisect
import math
import operator
import re


def number(value, label, low=-1e12, high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'meta的{label}必须是范围内的有限数值')
    return value


def latlon(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('meta位置必须是[纬度,经度]')
    return number(value[0], '纬度', -90, 90), number(value[1], '经度', -180, 180)


FUNCTIONS = {name: getattr(math, name) for name in (
    'sin','cos','tan','asin','acos','atan','atan2','sinh','cosh','tanh',
    'sqrt','exp','log','log10','floor','ceil','radians','degrees','fabs')}
FUNCTIONS.update(arcsin=math.asin, arccos=math.acos, arctan=math.atan, arctan2=math.atan2,
    deg2rad=math.radians, rad2deg=math.degrees, abs=abs,
    minimum=min, maximum=max, clip=lambda x,a,b: min(max(x,a),b))
BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
COMPARE = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
           ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}


class SafeCurve:
    """Interpret an allowlisted scalar math AST; never expose numpy modules."""
    def __init__(self, expression):
        if not isinstance(expression, str) or not 0 < len(expression) <= 4096:
            raise ValueError('meta曲线公式应为1～4096字符的数学表达式')
        try:
            self.tree = ast.parse(expression, mode='eval')
            nodes = list(ast.walk(self.tree))
        except (SyntaxError, RecursionError):
            raise ValueError('meta曲线公式语法错误或嵌套过深') from None
        if len(nodes) > 256:raise ValueError('meta曲线公式过于复杂')
        allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Tuple, ast.List,
            ast.BinOp, ast.UnaryOp, ast.UAdd, ast.USub, ast.Call, ast.Attribute,
            ast.IfExp, ast.Compare, ast.BoolOp, ast.And, ast.Or, *BINARY, *COMPARE)
        for node in nodes:
            if not isinstance(node, allowed):raise ValueError('meta曲线只允许白名单数学表达式，不支持脚本或文件操作')
            if isinstance(node, ast.Constant):number(node.value, '公式常量')
            if isinstance(node, ast.Name) and node.id not in {'t','pi','e','np','math',*FUNCTIONS}:
                raise ValueError('meta曲线引用了不允许的名称')
            if isinstance(node, ast.Attribute):
                if not isinstance(node.value, ast.Name) or node.value.id not in ('np','math') or node.attr not in {'pi','e',*FUNCTIONS}:
                    raise ValueError('meta曲线只支持指定的np/math数学函数与常量')
            if isinstance(node, ast.Call):
                if node.keywords or len(node.args) > 3 or self.function_name(node.func) not in FUNCTIONS:
                    raise ValueError('meta曲线函数或参数不受支持')

    @staticmethod
    def function_name(node):
        return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None

    def value(self, node, t):
        if isinstance(node, ast.Expression):return self.value(node.body, t)
        if isinstance(node, ast.Constant):return node.value
        if isinstance(node, ast.Name):
            if node.id == 't':return t
            if node.id in ('pi','e'):return getattr(math,node.id)
            raise ValueError('meta曲线不能直接引用函数或模块')
        if isinstance(node, ast.Attribute):
            if node.attr in ('pi','e'):return getattr(math,node.attr)
            raise ValueError('meta数学函数必须被调用')
        if isinstance(node, (ast.Tuple, ast.List)):
            if len(node.elts) != 2:raise ValueError('meta曲线需返回(纬度,经度)')
            return tuple(self.value(x,t) for x in node.elts)
        if isinstance(node, ast.IfExp):return self.value(node.body if self.value(node.test,t) else node.orelse,t)
        if isinstance(node, ast.BoolOp):
            values=[bool(self.value(x,t)) for x in node.values]
            return all(values) if isinstance(node.op,ast.And) else any(values)
        if isinstance(node, ast.Compare):
            first=self.value(node.left,t)
            for op,right in zip(node.ops,node.comparators):
                last=self.value(right,t)
                if not COMPARE[type(op)](first,last):return False
                first=last
            return True
        if isinstance(node, ast.UnaryOp):
            value=number(self.value(node.operand,t),'公式运算值')
            return -value if isinstance(node.op,ast.USub) else value
        if isinstance(node, ast.BinOp):
            a=number(self.value(node.left,t),'公式运算值');b=number(self.value(node.right,t),'公式运算值')
            if isinstance(node.op,ast.Pow) and abs(b)>16:raise ValueError('meta曲线幂指数超过限制')
            return number(BINARY[type(node.op)](a,b),'公式运算结果')
        if isinstance(node, ast.Call):
            args=[number(self.value(x,t),'函数参数') for x in node.args]
            return number(FUNCTIONS[self.function_name(node.func)](*args),'函数结果')
        raise ValueError('meta曲线表达式不受支持')

    def __call__(self, t):
        try:return latlon(self.value(self.tree,t))
        except (ArithmeticError, TypeError, RecursionError):
            raise ValueError('meta曲线计算失败，请检查数值范围、除数及数学函数参数') from None


def waypoint_path(target, radius):
    start=latlon(target.get('start'))
    speed=number(target.get('speed_kn'),'目标航速',0,200)*.514444
    waypoints=target.get('waypoints')
    if not isinstance(waypoints,list) or len(waypoints)>2000:
        raise ValueError('meta航点路径需要waypoints列表，最多2000个航点')
    points=[start];times=[0.0]
    for raw in waypoints:
        dest=latlon(raw);last=points[-1]
        p,q=math.radians(last[0]),math.radians(dest[0])
        h=math.sin((q-p)/2)**2+math.cos(p)*math.cos(q)*math.sin(math.radians(dest[1]-last[1])/2)**2
        distance=2*radius*math.asin(math.sqrt(min(1,max(0,h))))
        if distance<.001:continue
        times.append(times[-1]+distance/max(speed,1e-6));points.append(dest)
    def position(t):
        if len(points)==1 or speed==0:return start
        i=bisect.bisect_right(times,t)
        if i>=len(points):return points[-1]
        fraction=(t-times[i-1])/(times[i]-times[i-1])
        return tuple(a+(b-a)*fraction for a,b in zip(points[i-1],points[i]))
    return position


def reconstruct_meta(document):
    params=document.get('params',document)
    if not isinstance(params,dict):raise ValueError('meta.params必须是对象')
    if document.get('state','completed')!='completed':raise ValueError('meta样本尚未完成，不能作为完整轨迹数据')
    duration=number(params.get('T_MAX',600),'T_MAX',.001,604800)
    dt=number(params.get('DT',1),'DT',.001,604800)
    radius=number(params.get('EARTH_R',6371000),'EARTH_R',1e6,1e8)
    steps=math.floor((duration+1e-9)/dt)+1
    if not 2<=steps<=2000:raise ValueError('meta每目标采样需2～2000点，请调整DT或T_MAX')
    times=[i*dt for i in range(steps) if i*dt<=duration+1e-9]
    targets=params.get('targets');platforms=params.get('platforms')
    if not isinstance(targets,list) or not 1<=len(targets)<=100:raise ValueError('meta需要非空targets目标列表，最多100个')
    if len(times)*len(targets)>20000:raise ValueError('meta总采样点数超过20000，请调整DT或拆分目标')
    if not isinstance(platforms,list) or not 1<=len(platforms)<=8:raise ValueError('meta需要1～8个观测平台')
    observers=[]
    for platform in platforms:
        if not isinstance(platform,dict):raise ValueError('meta平台格式无效')
        name=platform.get('name')
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('meta平台编号无效')
        observers.append({'name':name,'start_ll':list(latlon(platform.get('start_ll')))})
    tracks=[];seen=set();colors={}
    for target in targets:
        if not isinstance(target,dict):raise ValueError('meta目标格式无效')
        tid=target.get('name')
        if not isinstance(tid,str) or not 1<=len(tid)<=80 or tid in seen:raise ValueError('meta目标编号无效或重复')
        seen.add(tid)
        path_type=target.get('path_type','waypoints')
        if path_type=='curve':
            latlon(target.get('start'))
            path=SafeCurve(target.get('curve_func'))
        elif path_type=='waypoints':path=waypoint_path(target,radius)
        else:raise ValueError('meta仅支持waypoints和curve路径，不会猜测未知运动模型')
        points=[]
        for t in times:
            lat,lon=latlon(path(t));points.append({'t':t,'x':lon,'y':lat})
        tracks.append({'id':tid,'branch':'main','points':points})
        if isinstance(target.get('color'),str) and re.fullmatch(r'#[0-9a-fA-F]{6}',target['color']):colors[tid]=target['color']
    return {'coordinate_system':'lon_lat','tracks':tracks},observers,duration,colors
