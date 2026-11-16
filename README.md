# 千问图片接入台 · FastAPI

独立项目：`C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client`。
网页上传图片 → FastAPI → 阿里云百炼千问视觉模型 → 返回原文与下载链接。
不改原BTR仿真器，暂不接轨迹反演、微调、调参或智能体调度。

## 启动

```powershell
cd "C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client"
python -m pip install -r requirements.txt
python app.py
```

也可以双击 `启动工具.cmd`（需要先安装上述依赖）。
- 网页：http://127.0.0.1:8870
- Swagger交互API文档：http://127.0.0.1:8870/docs
- OpenAPI契约：http://127.0.0.1:8870/openapi.json

默认只绑定127.0.0.1，不要直接暴露到公网。旧桌面版保留在desktop_app.py；app.py现在启动网页服务。

## 网页操作

1. 选择或拖入1～8张PNG/JPEG/WebP图片，建议选择样本input下的hull/tow图片。
2. 填写已开通的视觉模型名称（默认示例qwen3-vl-plus）及Base URL。
3. 填写API Key，或提前在服务端配置DASHSCOPE_API_KEY。密钥不在浏览器持久化，不写到结果文件。
4. 可先点“仅预检图片”：图片上传到本地FastAPI，但不调用百炼，不需要密钥。
5. 真实调用需勾选传输与费用确认，再点“发送给千问”。
6. 返回文本在右侧显示，可下载answer.txt、response.json、request_meta.json。

图片预览仅在浏览器本地。每次预检/发送的临时上传文件在处理结束后删除（进程被强制终止可能遗留work下文件）。
临时图片不会自动读取配套platform.json、truth、meta或semantic文件。选择真值图片仍会发送，请自行确认。

## 配置

- DASHSCOPE_API_KEY：百炼API Key，可选；请求头密钥优先于环境变量。
- QWEN_MODEL：默认模型名称，可选。
- QWEN_BASE_URL：默认接口地址，可选。

默认北京兼容地址：https://dashscope.aliyuncs.com/compatible-mode/v1
也支持对应地域业务空间地址。API Key地域必须匹配；模型是否可用以账号控制台为准。
只允许代码列明的官方HTTPS域名，不接受任意代理，不跟随重定向。
请勿把真实密钥写入Git或聊天消息。服务端环境变量优先用于长期嵌入；网页字段适合临时使用。

## 给后续智能体调用的接口

`POST /api/recognize`，Content-Type为multipart/form-data。

| 字段 | 位置 | 含义 |
| --- | --- | --- |
| files | 文件，可重复 | 1～8张图片 |
| prompt | 表单 | 提示词；最长50000字符 |
| model | 表单，可选 | 视觉模型名称，空值使用服务配置 |
| base_url | 表单，可选 | 百炼Base URL，空值使用服务配置 |
| dry_run | 表单，默认false | true仅预检，不调用云端 |
| consent | 表单，默认false | 真实调用必须true |
| X-Dashscope-Key | 请求头，可选 | 本次API Key；未填则读取服务端环境变量 |

预检示例（无需密钥）：

```powershell
curl.exe -X POST "http://127.0.0.1:8870/api/recognize" -F "files=@C:/完整路径/btr_A_hull.png" -F "files=@C:/完整路径/btr_A_tow.png" -F "dry_run=true"
```

智能体真实调用示例（服务端已经配置DASHSCOPE_API_KEY）：

```python
from pathlib import Path
import httpx

with Path('btr_A_hull.png').open('rb') as hull, Path('btr_A_tow.png').open('rb') as tow:
    response = httpx.post(
        'http://127.0.0.1:8870/api/recognize',
        files=[('files', ('hull.png', hull, 'image/png')),
               ('files', ('tow.png', tow, 'image/png'))],
        data={'consent': 'true', 'prompt': '描述图片中的亮带与不确定性。'},
        timeout=180,
    )
    response.raise_for_status()
    result = response.json()
    print(result['answer'])
```

成功响应：

```json
{
  "schema_version": "1.0",
  "ok": true,
  "dry_run": false,
  "result_id": "20261116_120000_abcdef12",
  "answer": "模型回复原文",
  "usage": {"total_tokens": 100},
  "downloads": {"answer.txt": "/api/results/20261116_120000_abcdef12/answer.txt"}
}
```

另有GET /api/health、GET /api/results/{result_id}/{filename}。
错误响应为HTTP错误状态与`{"detail":"说明"}`，字段校验错误不回显用户输入。
同步HTTP接口，推理在工作线程执行，不阻塞事件循环；同一进程最多2个识别处理请求。不是异步任务队列。

## 文件与边界

- web.py：路由、上传校验、响应契约与适配层。
- client.py：独立百炼调用模块，可被智能体直接复用，原CLI保持可用。
- static/：无构建步骤的HTML/CSS/JS上传页面。
- outputs/时间_编号/：回复正文、脱敏服务响应、无密钥/无图片Base64的请求元数据。
- work/：短期上传暂存，不在网页公开访问。

本工具限制单图10MiB、合计20MiB，请求体24MiB，响应16MiB；这些是应用限制，不是厂商完整限制。
不自动重试、没有幂等任务ID。断线/超时/关闭网页不能撤回已发请求，重复调用可能再次收费。
本机无登录认证；有本机访问权限者可调用API和下载已保存回复。跨站浏览器POST和不可信Host被拒绝。
未来开放局域网、反向代理或多用户部署前，必须增加身份认证、密钥隔离、持久队列与配额管理。
Swagger页面依赖其默认CDN资源，离线网络可能无法加载；/openapi.json和主上传页不依赖外部静态资源。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest -v
```

测试使用FastAPI TestClient和模拟云端响应，不调用百炼、不计费。
覆盖多图构造、预检、上传清理、调用适配、下载、超限、跨站拒绝、密钥不回显及原CLI模块。
真实云端调用需用户在本机配置API Key后自行确认发送；离线测试不代表模型识别效果已经验证。

接口依据：[百炼视觉理解](https://help.aliyun.com/zh/model-studio/vision)、[OpenAI兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。
