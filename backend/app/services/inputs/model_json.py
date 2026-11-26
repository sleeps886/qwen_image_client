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


def association_object(answer):
    """Choose a complete association document, never stitch drafts or repair values.

    The final fenced JSON block is an explicit output boundary. Earlier drafts
    are retained in the raw attempt, not merged. Human review remains mandatory.
    Multiple unfenced documents are ambiguous and are deliberately rejected.
    """
    if not isinstance(answer,str) or len(answer)>4_000_000:raise ValueError('关联正文无效或超过解析大小限制')
    text=answer.lstrip('\ufeff').strip()
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('关联JSON含重复字段：'+key)
            value[key]=item
        return value
    def constant(value):raise ValueError('关联JSON含非有限数值')
    decoder=json.JSONDecoder(object_pairs_hook=pairs,parse_constant=constant)
    try:
        value=decoder.decode(text)
    except json.JSONDecodeError:
        pass
    else:
        if not isinstance(value,dict):raise ValueError('关联回复须为JSON对象')
        return value,{'source':'strict_json'}
    blocks=list(re.finditer(r'^```(?:json)?[ \t]*\r?\n(.*?)^```[ \t]*$',text,flags=re.I|re.M|re.S))
    if blocks and not text[blocks[-1].end():].strip():
        final=blocks[-1]
        try:value=decoder.decode(final.group(1).strip())
        except json.JSONDecodeError:raise ValueError('最后的关联JSON代码块残缺或格式错误；未回退采用前面的草稿') from None
        if not isinstance(value,dict):raise ValueError('最后的关联JSON代码块须为对象')
        return value,{'source':'terminal_json_fence','ignored_prefix_chars':final.start(),
                      'fenced_blocks':len(blocks),'note':'提取回复末尾完整JSON代码块；前面的草稿与说明未作为关联结果，仍须人工确认'}
    # A prose prefix followed by exactly one complete JSON object is also safe.
    # Do not scan nested fragments after a malformed document.
    start=text.find('{')
    if start<0:raise ValueError('模型没有返回完整关联JSON对象；未自动重试')
    try:value,end=decoder.raw_decode(text,start)
    except json.JSONDecodeError:raise ValueError('关联JSON残缺或格式错误；未截取内部片段冒充完整结果') from None
    if text[end:].strip():raise ValueError('关联正文包含额外内容或多版未明确分隔的JSON；未任意选择或自动重试')
    if not isinstance(value,dict):raise ValueError('关联回复须为JSON对象')
    return value,{'source':'prefixed_json','ignored_prefix_chars':start,'note':'提取说明文字后的完整JSON对象，仍须人工确认'}
