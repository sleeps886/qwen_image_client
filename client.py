"""Minimal Alibaba Cloud Model Studio image client (standard library only)."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from config import DEFAULT_BASE_URL, DEFAULT_MODEL, model_settings
from trajectory import TRAJECTORY_PROMPT
DEFAULT_PROMPT = TRAJECTORY_PROMPT
MAX_FILE_BYTES = 10 * 1024 * 1024  # Conservative application limit, not vendor limit.
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 8


class ClientError(Exception):
    pass


class UpstreamTimeout(ClientError):
    pass


class UpstreamConnectionError(ClientError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the bearer credential to redirected destinations.
        return None


def endpoint(base_url):
    value = base_url.strip().rstrip('/')
    parsed = urllib.parse.urlsplit(value)
    host = (parsed.hostname or '').lower()
    proxy = host in ('www.dmxapi.cn', 'dmxapi.cn')
    allowed = proxy or host in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com',
                       'dashscope-us.aliyuncs.com') or bool(re.fullmatch(
        r'[a-z0-9-]+\.(?:cn-beijing|ap-southeast-1|us-east-1|ap-northeast-1)\.maas\.aliyuncs\.com', host))
    if (parsed.scheme != 'https' or not allowed or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None,443)):
        raise ClientError('仅允许百炼官方或明确配置的DMXAPI HTTPS地址，不接受账号信息或URL查询参数。')
    base_path = '/v1' if proxy else '/compatible-mode/v1'
    if parsed.path == base_path + '/chat/completions':
        return value
    if parsed.path != base_path:
        raise ClientError('Base URL路径不匹配：DMXAPI使用/v1，百炼使用/compatible-mode/v1。')
    return value + '/chat/completions'


def load_image(path):
    path = Path(path)
    if not path.is_file():
        raise ClientError(f'图片不存在：{path.name}')
    if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise ClientError(f'{path.name}为空或超过本工具单图10MiB限制。')
    raw = path.read_bytes()
    if len(raw)>MAX_FILE_BYTES:
        raise ClientError('读取图片后超过大小限制。')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif raw.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise ClientError(f'{path.name}不是支持的PNG/JPEG/WebP图片。')
    return raw, mime


def build_request(images, prompt, model):
    if not model.strip() or not prompt.strip():
        raise ClientError('模型名称和提示词不能为空。')
    if len(prompt)>50000:
        raise ClientError('提示词超过本工具50000字符限制。')
    if not 1 <= len(images) <= MAX_IMAGES:
        raise ClientError(f'请选择1～{MAX_IMAGES}张图片。')
    contents, metadata, total = [], [], 0
    for path in images:
        raw,mime = load_image(path)
        total += len(raw)
        if total>MAX_TOTAL_BYTES:
            raise ClientError('图片合计超过本工具20MiB限制。')
        contents.extend([dict(type='text',text=f'图片 {len(metadata)+1}：{Path(path).name}'),
                         dict(type='image_url',image_url=dict(url=f'data:{mime};base64,'+base64.b64encode(raw).decode('ascii')))])
        metadata.append(dict(name=Path(path).name,bytes=len(raw),mime=mime,sha256=hashlib.sha256(raw).hexdigest()))
    contents.append(dict(type='text',text=prompt))
    return dict(model=model.strip(),messages=[dict(role='user',content=contents)],stream=False),metadata


def sample_images(directory):
    root=Path(directory)
    if root.name!='input':
        root=root/'input'
    paths=sorted(root.glob('btr_*_hull.png'))+sorted(root.glob('btr_*_tow.png'))
    if not paths:
        raise ClientError('未找到input目录下的hull/tow图片。不会自动读取truth、meta或semantic。')
    return paths


def redact(value, key):
    if isinstance(value,str):
        return value.replace(key,'[REDACTED]') if key else value
    if isinstance(value,list):
        return [redact(v,key) for v in value]
    if isinstance(value,dict):
        return {redact(k,key):redact(v,key) for k,v in value.items()}
    return value


def answer_text(response):
    try:
        message=response['choices'][0]['message']
        content=message.get('content')
        if isinstance(content,str):
            return content
        if isinstance(content,list):
            return '\n'.join(x.get('text','') for x in content if isinstance(x,dict))
        if message.get('refusal'):
            return str(message['refusal'])
    except (KeyError,IndexError,TypeError):
        pass
    raise ClientError('服务返回了无法识别的响应结构，请核对模型与接口；请求未自动重试。')


def send_images(images, prompt=DEFAULT_PROMPT, model=DEFAULT_MODEL,
                base_url=DEFAULT_BASE_URL, api_key=None, output_dir=None, timeout=None):
    key=(api_key or model_settings().api_key).strip()
    if not key:
        raise ClientError('请在后端.env配置MODEL_API_KEY。')
    if '\r' in key or '\n' in key:
        raise ClientError('API Key格式无效。')
    url=endpoint(base_url)
    if timeout is not None and (type(timeout) not in (int,float) or not 1<=timeout<=600):
        raise ClientError('显式网络超时参数应为None或1～600秒。')
    body,metadata=build_request(images,prompt,model)
    # Check local output access before spending on inference.
    out=Path(output_dir or Path(__file__).parent/'outputs')
    run=out/(datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:8])
    run.mkdir(parents=True,exist_ok=False)
    req=urllib.request.Request(url,json.dumps(body,ensure_ascii=False).encode('utf-8'),
        {'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as reply:
            raw=reply.read(16*1024*1024+1)
        if len(raw)>16*1024*1024:
            raise ClientError('响应超过本工具16MiB限制。')
        response=redact(json.loads(raw),key)
        text=answer_text(response)
    except urllib.error.HTTPError as exc:
        # Do not echo server bodies: they could contain credentials/image data.
        hints={400:'请求格式、图片或模型参数不被支持',401:'API Key无效或不属于当前模型服务',
               403:'服务拒绝访问，请检查中转站密钥、模型ID、调用权限或额度',404:'模型名称或接口地址不存在',
               413:'图片请求过大',429:'请求受限或额度不足'}
        raise ClientError(f'模型服务HTTP {exc.code}：{hints.get(exc.code,"服务请求失败")}。未自动重试。') from None
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        timed_out=isinstance(exc,TimeoutError) or isinstance(getattr(exc,'reason',None),TimeoutError)
        if timed_out:
            reason=f'网络等待超过显式设定的{timeout}秒。' if timeout is not None else '本地未设置等待时限，但上游或网络报告了超时。'
            raise UpstreamTimeout(reason+'请查看中转站调用记录；请求可能已计费，不会自动重试。') from None
        raise UpstreamConnectionError('无法连接模型服务或连接中断。请求可能已被处理，请核对中转站调用记录，不会自动重试。') from None
    except (json.JSONDecodeError,UnicodeDecodeError):
        raise ClientError('服务未返回有效JSON。请核对官方接口地址；未自动重试。') from None
    record=dict(created_at=datetime.now(timezone.utc).isoformat(),endpoint=url,model=model,
                prompt=redact(prompt,key),images=metadata,usage=response.get('usage'),
                response_id=response.get('id'),request_id=response.get('request_id'))
    try:
        (run/'response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding='utf-8')
        (run/'answer.txt').write_text(text,encoding='utf-8')
        (run/'request_meta.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    except OSError:
        raise ClientError('模型已返回，但本地保存失败；请检查磁盘与目录权限，不要直接重试以免重复计费。') from None
    return dict(answer=text,output_dir=str(run.resolve()),usage=response.get('usage'))


def main():
    parser=argparse.ArgumentParser(description='发送本地图片到阿里云百炼千问视觉模型')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--images',nargs='+')
    group.add_argument('--sample',help='仿真sample目录，只读取input下图片')
    parser.add_argument('--model',default=model_settings().model)
    parser.add_argument('--base-url',default=model_settings().base_url)
    parser.add_argument('--prompt',default=DEFAULT_PROMPT)
    parser.add_argument('--output')
    parser.add_argument('--dry-run',action='store_true',help='仅校验并显示文件信息，不联网')
    parser.add_argument('--send',action='store_true',help='明确允许向百炼发送所选图片并产生API费用')
    args=parser.parse_args()
    try:
        images=sample_images(args.sample) if args.sample else args.images
        endpoint(args.base_url)
        _,metadata=build_request(images,args.prompt,args.model)
        if args.dry_run:
            print(json.dumps(dict(dry_run=True,model=args.model,endpoint=endpoint(args.base_url),images=metadata),ensure_ascii=False,indent=2))
        elif not args.send:
            parser.error('真实云端调用需显式添加 --send；预检请使用 --dry-run')
        else:
            result=send_images(images,args.prompt,args.model,args.base_url,output_dir=args.output)
            print(result['answer']);print('\n结果保存：'+result['output_dir'])
    except (ClientError,OSError,ValueError) as exc:
        parser.exit(1,'错误：'+str(exc)+'\n')


if __name__=='__main__':
    main()
