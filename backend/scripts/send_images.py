"""Explicit image CLI: python -m backend.scripts.send_images --help."""
import argparse
import json
from backend.app.core.config import model_settings
from backend.app.infrastructure.llm.client import (ClientError,DEFAULT_PROMPT,sample_images,endpoint,build_request,send_images)

def main():
    parser=argparse.ArgumentParser(description='发送本地图片到阿里云百炼千问视觉模型')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--images',nargs='+')
    group.add_argument('--sample',help='仿真sample目录，只读取input下图片')
    parser.add_argument('--model',default=model_settings().model)
    parser.add_argument('--base-url',default=model_settings().base_url)
    parser.add_argument('--api-format',choices=('openai','anthropic'),default=model_settings().api_format)
    parser.add_argument('--prompt',default=DEFAULT_PROMPT)
    parser.add_argument('--output')
    parser.add_argument('--dry-run',action='store_true',help='仅校验并显示文件信息，不联网')
    parser.add_argument('--send',action='store_true',help='明确允许向百炼发送所选图片并产生API费用')
    args=parser.parse_args()
    try:
        images=sample_images(args.sample) if args.sample else args.images
        endpoint(args.base_url,args.api_format)
        _,metadata=build_request(images,args.prompt,args.model,api_format=args.api_format)
        if args.dry_run:
            print(json.dumps(dict(dry_run=True,model=args.model,api_format=args.api_format,endpoint=endpoint(args.base_url,args.api_format),images=metadata),ensure_ascii=False,indent=2))
        elif not args.send:
            parser.error('真实云端调用需显式添加 --send；预检请使用 --dry-run')
        else:
            result=send_images(images,args.prompt,args.model,args.base_url,output_dir=args.output,api_format=args.api_format)
            print(result['answer']);print('\n结果保存：'+result['output_dir'])
    except (ClientError,OSError,ValueError) as exc:
        parser.exit(1,'错误：'+str(exc)+'\n')


if __name__=='__main__':
    main()
