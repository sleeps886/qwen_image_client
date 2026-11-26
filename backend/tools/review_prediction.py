"""Terminal client for the same durable trajectory interrupt (no model call)."""
import argparse
import json
from urllib.request import Request,urlopen


def main():
    parser=argparse.ArgumentParser(description='查看并采用/打回轨迹候选，不调用模型')
    parser.add_argument('session_id');parser.add_argument('--base-url',default='http://127.0.0.1:8870')
    args=parser.parse_args()
    url=args.base_url.rstrip('/')+'/api/incremental/sessions/'+args.session_id
    with urlopen(url,timeout=10) as response:state=json.load(response)
    if state['status']!='awaiting_review':
        print('当前无待确认轨迹：'+state['status']);return
    print('interrupt：虚线候选待确认。请先在工作台核对图形。')
    choice=input('是否采用？输入 y 采用，n 打回，其他输入保留暂停：').strip().lower()
    if choice not in ('y','n'):print('仍保持暂停。');return
    reason=input('打回原因：').strip() if choice=='n' else ''
    if choice=='n' and not reason:print('原因不能为空，仍保持暂停。');return
    payload=json.dumps({'version':state['version'],'adopt':choice=='y','reason':reason}).encode()
    request=Request(url+'/review',data=payload,headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=10) as response:result=json.load(response)
    print('已采用并定稿。' if choice=='y' else '已打回恢复；请重选点位后手动运行，未自动重试。')
    print('版本：',result['version'],'调用次数：',result['calls'])


if __name__=='__main__':main()
