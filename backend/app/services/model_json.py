"""Extract one complete model JSON contract; never repair values or execute text."""
import json
import re


def model_object(answer,coordinate_system):
    if not isinstance(answer,str) or len(answer)>4_000_000:
        raise ValueError('模型正文无效或超过JSON解析大小限制')
    text=answer.lstrip('\ufeff').strip()
    if not text:raise ValueError('模型没有返回正文，无法提取结构化配对；未自动重试')
    if '{' not in text:
        raise ValueError('模型只返回说明文字，没有输出'+coordinate_system+' JSON对象；不能从描述或省略号猜测配对，原文已保存，未自动重试')
    try:
        document=json.loads(text)
    except json.JSONDecodeError:
        document=None
    else:
        if not isinstance(document,dict):raise ValueError('模型契约必须是JSON对象')
        return document
    # Prefer explicit code blocks, but do not silently choose a final block
    # among multiple competing contracts or fix malformed/truncated JSON.
    blocks=re.findall(r'```(?:json)?[ \t]*\r?\n(.*?)```',text,flags=re.I|re.S)
    valid=[]
    for block in blocks:
        try:value=json.loads(block.strip())
        except json.JSONDecodeError:continue
        if isinstance(value,dict) and value.get('coordinate_system')==coordinate_system:valid.append(value)
    if len(valid)>1:raise ValueError('模型正文包含多个契约JSON对象，无法唯一确定；未自动选择或重试')
    # Scan complete top-level objects to also support explanatory prefixes.
    # Advancing past decoded objects avoids counting their nested fields again.
    decoder=json.JSONDecoder();found=[];index=0;attempts=0
    while index<len(text):
        start=text.find('{',index)
        if start<0:break
        attempts+=1
        if attempts>2000:raise ValueError('模型JSON解析候选过多，停止解析')
        try:value,end=decoder.raw_decode(text,start)
        except json.JSONDecodeError:
            next_start=text.find('{',start+1)
            head=text[start:next_start if next_start>=0 else len(text)]
            if re.search(r'"coordinate_system"\s*:\s*"'+re.escape(coordinate_system)+r'"',head):
                raise ValueError('模型契约JSON残缺或格式错误，未截取内部片段冒充完整结果') from None
            index=start+1;continue
        index=end
        if isinstance(value,dict) and value.get('coordinate_system')==coordinate_system:found.append(value)
        if len(found)>1:raise ValueError('模型正文包含多个契约JSON对象，无法唯一确定；未自动选择或重试')
    if len(found)==1:return found[0]
    raise ValueError('未找到唯一、完整的'+coordinate_system+' JSON对象；正文可能格式错误或被截断，已保留原文，未自动重试')
