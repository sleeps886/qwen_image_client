"""Serialize Agent diagnostics without exposing private array fields."""
import numpy as np


def public(value):
    if isinstance(value,dict):return {k:public(v) for k,v in value.items() if not k.startswith('_')}
    if isinstance(value,(tuple,list)):return [public(v) for v in value]
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    return value
