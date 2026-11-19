"""Shared Agent state contracts; credentials are not checkpoint state."""
from typing import Any,TypedDict


class RecognitionState(TypedDict,total=False):
    request_id:str
    mode:str
    images:list[str]
    prompt:str
    context:dict[str,Any]|None
    model_result:dict[str,Any]
    answer:str
    output_dir:str
    tracks_document:dict[str,Any]


class EvidenceState(TypedDict,total=False):
    images:list[str]
    context:dict
    prompt:str
    rules:list[dict]
    evidence:dict
    answer:str
    observations:dict
    trace:list[str]
    tracklets:list[dict]
    count_hypothesis:dict
    count_report:dict
    model_observations:dict
    identity_report:dict
    crossing_report:dict
    measurement_report:dict


class TrackingState(TypedDict,total=False):
    context:dict
    observations:dict
    evidence:dict
    trace:list[str]
    raw_document:dict
    document:dict
    tracking_report:dict
    reprojection_report:dict
    failure:str


class InverseState(TypedDict,total=False):
    paths:list
    context:dict
    evidence:dict
    ridges:dict
    proposals:dict
    fitted:dict
    selection:dict
    quality:dict
    document:dict
    rules:list
    profile_report:dict
