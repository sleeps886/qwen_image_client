# 千问图片接入台 · FastAPI

独立项目：`C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client`。
网页上传hull/tow图片与可选已知平台参数 → FastAPI → 千问返回轨迹点JSON → 程序绘制估计轨迹PNG。
不改原BTR仿真器。当前由模型估计轨迹点，不接传统数值反演，不做微调、调参或智能体调度。模型位置估计尚未用真值验证。

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
2. 在项目根目录.env中配置API Key、模型与Base URL，修改后重启服务。
3. 网页不再展示或接收API Key、模型名称、Base URL；只显示后端是否已配置密钥。
4. 可选上传同一样本input/platform.json，提供平台位置、航向、速度与阵列几何；这个文件会与提示词一起发送至百炼。不要上传meta或semantic。
5. 可先点“仅预检图片”：图片上传到本地FastAPI，但不调用百炼，不需要密钥。
5. 真实调用需勾选传输与费用确认，再点“发送给千问”。
6. 返回文本在右侧显示，可下载answer.txt、response.json、request_meta.json。

图片预览仅在浏览器本地。每次预检/发送的临时上传文件在处理结束后删除（进程被强制终止可能遗留work下文件）。
临时图片不会自动读取配套文件。只有用户主动选择的platform.json会作为已知平台信息发送；服务拒绝含params或targets的元数据格式。选择真值图片仍会发送，请自行确认。

## 配置

- DASHSCOPE_API_KEY：百炼API Key，真实调用必填。
- QWEN_MODEL：默认模型名称，可选。
- QWEN_BASE_URL：默认接口地址，可选。

默认北京兼容地址：https://dashscope.aliyuncs.com/compatible-mode/v1
也支持对应地域业务空间地址。API Key地域必须匹配；模型是否可用以账号控制台为准。
只允许代码列明的官方HTTPS域名，不接受任意代理，不跟随重定向。
请勿把真实密钥写入Git或聊天消息。启动时读取本项目.env（与当前工作目录无关），进程环境变量优先于.env，禁止变量插值；修改后重启服务。

## 给后续智能体调用的接口

`POST /api/recognize`，Content-Type为multipart/form-data。

| 字段 | 位置 | 含义 |
| --- | --- | --- |
| files | 文件，可重复 | 1～8张图片 |
| prompt | 表单 | 提示词；最长50000字符 |
| platform | 文件，可选 | input/platform.json，最大100KB；不含目标真值 |
| dry_run | 表单，默认false | true仅预检，不调用云端 |
| consent | 表单，默认false | 真实调用必须true |

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
真实云端调用需用户在本机.env配置API Key后自行确认发送；离线测试不代表模型识别效果已经验证。

接口依据：[百炼视觉理解](https://help.aliyun.com/zh/model-studio/vision)、[OpenAI兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。


## 轨迹生成（本次更新）

默认提示词已从“描述图片”切换为“根据图片输出轨迹点JSON”。重新启动FastAPI服务并刷新页面后生效。
用户仍可改提示词，但不符合下面契约时，只保留模型原文，页面明确提示没有可绘制轨迹，不会自动重试。

模型输出契约：
```json
{
  "coordinate_system": "local_xy_m",
  "position_observable": true,
  "warnings": ["左右舷镜像等限制"],
  "tracks": [{"id":"T1","branch":"main","points":[{"t":0,"x":100,"y":200},{"t":5,"x":110,"y":205}]}]
}
```
坐标系：local_xy_m以第一个已知平台初始位置为原点（东/北米）；lon_lat为经/纬度；bearing_time为相对舷角/时间（此时y必须等于t）。
空间轨迹必须提供已知平台参数且模型声明position_observable=true；这只是一道格式/前提门槛，不是位置可观性的物理证明。
不能可靠定位时，模型须返回bearing_time，不得编造地理曲线；前端醒目标为“仅方位—时间”，不能与真实地理轨迹混淆。
没有任何可提取点则返回空tracks与原因，服务不生成伪轨迹。

新增输出：estimated_track.png、tracks.json，API增加trajectory_image、trajectory_status、warnings。
图片由Matplotlib根据模型输出的有限数值绘制，不执行模型生成的Python/SVG代码，不是调用文生图模型。
图像标题明确标为MODEL ESTIMATE，不使用输入真值。绘图里的Track序号按JSON列表顺序对应id/branch；地图上黄色方块仅是平台初始位置，不是平台完整航迹。
无法关联的断续段要求拆为不同条目，程序不会跨条目补线；条目内按时间连接，因此模型采样过稀仍可能造成曲线失真。

尚未验证：千问能否从本项目声呐图稳定、准确地恢复位置与机动。图像/平台信息不充分时会存在距离与左右舷歧义，仅修改提示词不能保证得到真实轨迹。
当前位置/方位点都由模型估计，没有调用传统反演或真值修正。必须与独立truth进行匹配、误差和物理一致性评测后才能判断效果。


## .env后端配置（最新）

已提供.env（密钥为空）和.env.example模板，均位于本项目目录。

```dotenv
DASHSCOPE_API_KEY=
QWEN_MODEL=qwen3-vl-plus
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

仅在本机.env的DASHSCOPE_API_KEY等号后填写真实密钥，示例文件不得放真实密钥。
.env及其变体已被Git忽略，.env.example可提交。不要截图或分享填好后的.env。
若系统已配置同名环境变量，其值优先，.env不会覆盖（包括系统中的空值）。
网页与识别API不再接受model、base_url、api_key或X-Dashscope-Key覆盖，旧调用方式返回400，智能体只需上传图片及任务字段。
/api/health只返回key_configured布尔状态，不返回模型名称、接口地址或密钥。
回复/请求元数据可能仍含实际调用的模型与接口记录，这是实验结果追溯，不是前端配置入口；密钥不会写入这些结果文件。
CLI保留显式参数用于本机开发；旧desktop_app.py保留兼容，不是当前网页入口。


## DMXAPI第三方兼容服务

本机.env可设置QWEN_BASE_URL=https://www.dmxapi.cn/v1；程序自动请求/v1/chat/completions。
这不是智谱或阿里云官方域名；使用时图片、平台参数、提示词和认证密钥会发送给DMXAPI服务。
DASHSCOPE_API_KEY沿用旧变量名，此时必须填写DMXAPI签发的密钥，不是百炼密钥。
QWEN_MODEL也沿用旧变量名，但值须为DMXAPI实际支持且可接收图片的模型ID；供应商显示名Zhipu GLM不是模型ID。
本次只修改配置与兼容路径，没有向该服务发送真实请求，也未验证其模型可用性。
