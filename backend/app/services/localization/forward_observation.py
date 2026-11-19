"""Side-effect-free observation model matching the GUI generator conventions.

Independent from HTTP, providers, reference labels and generator module globals.
This model does not claim that folded observations have a unique inverse.
"""
from dataclasses import dataclass
import math


def _finite(*values):
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise ValueError('正向模型参数必须是有限数值')


def _ll(value):
    if not isinstance(value,(tuple,list)) or len(value)!=2:raise ValueError('经纬度必须包含lat/lon')
    _finite(*value)
    if not -90<=value[0]<=90 or not -180<=value[1]<=180:raise ValueError('经纬度越界')


@dataclass(frozen=True)
class GeoReference:
    latitude: float
    longitude: float
    radius_m: float = 6371000.

    def __post_init__(self):
        _ll((self.latitude,self.longitude));_finite(self.radius_m)
        if abs(self.latitude)>=85 or self.radius_m<=0:raise ValueError('局部投影参考或地球半径不支持')

    def to_xy(self,ll):
        _ll(ll)
        return (self.radius_m*math.cos(math.radians(self.latitude))*math.radians(ll[1]-self.longitude),
                self.radius_m*math.radians(ll[0]-self.latitude))

    def to_ll(self,xy):
        if not isinstance(xy,(tuple,list)) or len(xy)!=2:raise ValueError('局部坐标必须包含east/north')
        _finite(*xy)
        ll=(self.latitude+math.degrees(xy[1]/self.radius_m),
            self.longitude+math.degrees(xy[0]/(self.radius_m*math.cos(math.radians(self.latitude)))))
        _ll(ll)
        return ll


def initial_bearing(observer,target):
    _ll(observer);_ll(target)
    p1,p2=map(math.radians,(observer[0],target[0]));dl=math.radians(target[1]-observer[1])
    y=math.sin(dl)*math.cos(p2)
    x=math.cos(p1)*math.sin(p2)-math.sin(p1)*math.cos(p2)*math.cos(dl)
    if abs(x)+abs(y)<1e-14:raise ValueError('观测点与目标重合或方位不唯一')
    return math.degrees(math.atan2(y,x))%360


def fold_relative(angle):
    _finite(angle)
    angle=angle%360
    return min(angle,360-angle)


def array_location(platform,offset,time,reference):
    _ll(platform['start_ll']);_finite(time,platform['heading_deg'],platform['speed_kn'])
    if time<0:raise ValueError('观测时间不能为负')
    if not isinstance(offset,(tuple,list)) or len(offset)!=2:raise ValueError('阵列偏移必须包含forward/port')
    _finite(*offset)
    east,north=reference.to_xy(platform['start_ll'])
    psi=math.radians(90-platform['heading_deg']);c,s=math.cos(psi),math.sin(psi)
    forward=platform['speed_kn']*1852/3600*time+offset[0]
    return reference.to_ll((east+forward*c-offset[1]*s,north+forward*s+offset[1]*c))


def observe(platform,target,time,reference):
    _ll(target)
    result={}
    for array in ('hull','tow'):
        location=array_location(platform,platform[array+'_m'],time,reference)
        bearing=initial_bearing(location,target)
        result[array+'_ll']=location
        result[array+'_deg']=fold_relative(bearing-platform['heading_deg'])
    return result
