# 千问图片接入台 · FastAPI

独立项目：`C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client`。
网页上传hull/tow图片与可选已知平台参数 → FastAPI → 千问返回轨迹点JSON → 程序绘制估计轨迹PNG。
不改原BTR仿真器。图片+平台参数模式已接入LangGraph、本地规则检索、像素候选与模型编号关联，然后进行双阵空间求交。已加入持续亮带数量线索、目标清单/定位覆盖复核、局部窗口连续估计与重投影检查；前端显示真实节点状态。真实模型准确率待验证。详见[Agent阶段说明](docs/agent-evidence.md)。

## 启动

### 新增：完整正向反演（实验，本地零模型调用）

若此前在“全局归属与有界迭代”遇到120秒预算错误，已加入不变模板/矩阵缓存、细化阶段预算和明确的部分结果状态；本机真实浏览器复测约25秒完成，详见[超时修复记录](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/docs/inverse-timeout-fix.md)。总预算仍有限，部分优化不等于精度验收通过。

2026-11-18：按指定162346批次sample_0005反复对照后的更新见[11轮优化与实际误差记录](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/docs/sample005-iteration-results.md)。新增实际图像联合拟合、有符号周期背景及锁定身份内的运动族比较；主图为明确标记的代表假设，`tracks.json`与`mirror_candidates.png`仍保留全部镜像，不能把主图视为唯一已确认解。

上传同一样本的原始hull/tow两图和`input/platform.json`，在“识别流程”选择“完整正向反演（工具原图，实验）”，点击执行。默认不需要勾选云端费用同意、不需要模型Key；可选云端假设复核必须另行授权，最多一次，不覆盖程序坐标。支持真实九节点进度、BTR回投、诊断下载、历史和停止。接口为`POST /api/invert`及`POST /api/invert/stream`。

**尚未通过完整精度验收**：三个同批次工具样本的数值身份数为3/3/4，标签均有4个目标；镜像和部分公里级偏差仍存在。旧流程保留默认，不自动推广。详情见[完整链路实现与实测](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/docs/forward-inverse-completion.md)。案例知识库仍是独立设计，不属于已上线功能。

项目已按前后端划分：`backend/`存放Python后端、Agent、工作流、脚本和测试，`frontend/`存放HTML、JS、CSS。
目录见[前后端架构说明](docs/architecture.md)，Agent位于`backend/app/agents/sonar_agent.py`。
当前图片空间流程运行LangGraph证据图；单文件绘图保持直接执行。暂不支持跨请求断点恢复。

```powershell
cd "C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client"
python -m pip install -r requirements.txt
python -m backend.app.main
```

也可以双击 `启动工具.cmd`（需要先安装上述依赖）。
- 网页：http://127.0.0.1:8870
- Swagger交互API文档：http://127.0.0.1:8870/docs
- OpenAPI契约：http://127.0.0.1:8870/openapi.json

默认只绑定127.0.0.1，不要直接暴露到公网。旧命令`python app.py`作为薄兼容入口保留。
旧桌面版改用`python -m backend.app.legacy.desktop_app`；图片CLI改用`python -m backend.scripts.send_images`。

## 网页操作

### 运行完成后的结果区

成功绘图后优先展示结果摘要、轨迹图、PNG/轨迹点JSON下载和可读用量卡片。
推理文本、原始正文、告警详情、调试文件和日志自动折叠，但内容不删除，仍可展开或下载。
运行中保留实时正文、服务端推理文本、日志与停止按钮；完成后隐藏停止按钮。
没有有效轨迹图时会显示“模型已返回，未生成轨迹图”，并展开正文与告警，不冒充出图成功。
历史结果会从已保存的响应恢复用量（如存在），不为展示新界面重新调用模型。

### 新增：只上传一份目标数据文件（不传图片）

重启后端并刷新网页，在“输入模式”选择“单文件绘图（无需图片）”，在原来的平台文件入口
选择一份 JSON／JSONL／简单 JS 文件，先点“仅预检数据”。预检通过后勾选传输与费用确认，
点击“发送数据并生成轨迹图”。切换到该模式后，即使之前选过图片，也不会发送这些图片。
仍走真实模型接口（支持流式输出）；模型返回轨迹JSON后，由本地程序生成PNG，可下载、导入历史结果。
只选数据文件而没有图片时，页面会自动切换到单文件模式；点击发送时也会再次检测，不会被“请先选择图片”拦住。
选择名为`meta.json`、`semantic.json`或JSONL目标数据文件时，即使之前保留了已选图片，也会自动切到单文件模式；残留图片不发送。
若手工通过API在图片模式提交目标元数据，后端会明确提示切换模式，而不是仅报平台JSON格式错误。
测试使用模拟上游，尚未进行本次功能的真实付费调用。

支持以下格式：

- **`meta.json` 完整轨迹重建（新增）**：读取`params.targets`中的`start`、`speed_kn`、
  `path_type`与`waypoints`或`curve_func`，根据`T_MAX`与`DT`从全局t=0采样。
  `params.platforms`提供平台位置，无需另传`platform.json`。也接受没有外层`params`的同结构参数JSON。
  航点路径按球面距离计算各段时间、经纬度分段线性插值，到达最后航点后停留；
  曲线公式用独立的白名单数学AST解释器逐时刻计算，不使用eval/exec、不暴露numpy模块，
  不执行脚本、不跟随`OUT_DIR`或`files`读取／写入任意路径。
  支持常见sin/cos等np/math标量数学函数；未知语法、越界坐标、未完成样本等会明确拒绝。
  与原仿真轨迹图一致，绘制全仿真时段，不按`t_on/t_off`裁剪或改变曲线相位。
  时间不超过`T_MAX`；DT不整除时与仿真采样一致，不强行补出终点。
  每目标2～2000个采样点，合计最多20000点，超限提示调整DT或拆分，不静默减损完整轨迹。
  **完整采样保留本地出图，仅少量核对点发给模型**（通常每目标9点，目标多时减少）。
  模型核对点通过一致性校验后，`tracks.json`和PNG使用本地完整采样，而不是稀疏核对点。
  图中明确标注meta重建：不是模型从声呐图独立推断的结果，也不是盲测。
  示例样本T_MAX=600、DT=1、4个目标可生成2404个完整采样点；不会因模型输入长度限制只剩起终点。
- `semantic.json` 仿真参考摘要：`platforms[].self`是观测平台，
  `platforms[].targets[].kinematics`才包含T1等目标的起终点。该文件本身已含平台信息，
  单独选择这一份即可，不需要再上传 `platform.json`。程序按目标ID去重，绘制每个目标
  在全仿真t=0和t=T_MAX时的位置；圆点为起点、叉号为终点、方块为平台。
  这些端点不在 `observation_window_s` 的起止时刻，不能混用时间。
  摘要不含逐时刻轨迹，因此只画点、不连线，不会凭摘要声称还原完整运动路径。
  如需完整曲线，请手动提供真实逐时刻JSON/JSONL点位；绝不会自动跟随摘要中的磁盘路径读取文件。
- 本项目的 `platform.json` 格式：将 `platforms` 中的 A 等对象作为用户指定的绘图目标，
  使用 `start_ll`、`heading_deg`、`speed_kn` 和 `T_MAX` 按恒定航向、恒定航速推算首尾点；
  当前使用局部平面近似。速度为0时首尾位置相同，图片显示静止点，不会凭空补出运动轨迹。
  这是对象参数绘图，不是由声呐图反演目标，也不是独立盲测。
- 点位JSON：`{"coordinate_system":"lon_lat","tracks":[{"id":"A","points":[{"t":0,"x":122,"y":30}]}]}`。
  每个对象可以有1～2000点；多点时间必须严格递增。也可用顶层 `points` 表示单个对象。
  `lon_lat` 的x为经度、y为纬度；`local_xy_m` 的x/y为用户提供的局部东/北坐标（米）；
  `bearing_time` 的x为0～180度折叠舷角、y和t均为秒，不能当作地理轨迹。
- JSON点位数组／JSONL：每条记录含 `target_id`（可选，默认T1）、`time_s`（或t）、`lon`、`lat`。
  手动上传的目标真值也可用于数据绘图，但不再属于盲测；不会自动读取配套真值文件。
- 简单JS：`const points = <严格JSON>;`、`export default <严格JSON>;` 或
  `module.exports = <严格JSON>;`。不支持函数、计算表达式、单引号对象或任意脚本；绝不执行JS。

文件上限1MiB，发送模型的有效点位上限40000字符，提示词与数据合计最多50000字符。
meta的完整采样不受这个模型文本字符上限影响（保留本地，只发送核对点），仍受上述采样点数上限约束。
超限会提示拆分，不会静默删点。只将白名单内的绘图数据作为文本发送给模型，不上传原始脚本或自动读取其他文件。
模型改变目标、点数、坐标系或点位时拒绝绘图，保存回复并提示问题，不自动重试付费请求。
页面提供示例下载，示例文件位于本项目的 `examples/target.json`。

接口沿用 `/api/recognize`（同步／预检）和 `/api/recognize/stream`（SSE）：
`input_mode=data`、`platform=<数据文件>`，不传 `files`；真实调用仍需 `consent=true`。
同步预检使用 `dry_run=true`，不调用模型。`input_mode=images` 保留原来的图片功能；
API省略模式时，有图片按图片模式，无图片且有数据文件时按单文件模式处理。

### 图片识别（原有模式）

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

### Anthropic Messages 与 OpenAI 兼容协议

如果同一模型在Claude Code的Anthropic格式下正常，而项目用OpenAI接口收到403，
不能只依据该403判断模型在所有接口上都无权限。需核对协议、地址和令牌。
项目现支持两套独立协议，真实发送时只调用配置的协议一次，不自动切换或重试。

当前项目已保持原有 `MODEL_NAME`、`MODEL_BASE_URL` 与密钥，只增加：

```dotenv
MODEL_API_FORMAT=anthropic
MODEL_AUTH_MODE=bearer
MODEL_MAX_TOKENS=8192
```

修改后必须停止旧服务、重新启动，再刷新页面。“后端配置已就绪”旁会显示
`Anthropic Messages` 或 `OpenAI兼容`，运行日志及 `request_meta.json` 也记录协议。
只显示“已配置”不代表密钥已通过服务端权限验证。

- Anthropic模式：调用 `/v1/messages`，独立顶层 `system`、必填 `max_tokens`，图片使用
  `image/source/base64`；支持 `message_start`、`content_block_delta`、`message_delta`、
  `message_stop` 和错误事件。`text_delta`进入正文，服务公开的`thinking_delta`单独显示。
  必须收到结束事件才能标记流完成。`MODEL_REASONING_EFFORT`不会传到Anthropic接口。
- `MODEL_AUTH_MODE=bearer`：使用`Authorization: Bearer ...`，适用于使用Auth Token的中转入口。
  `MODEL_AUTH_MODE=x-api-key`：使用`x-api-key`，适用于Anthropic API Key。
  两种模式都带 `anthropic-version: 2023-06-01`。仅采用明确配置的一种鉴权头，不探测重试。
- 支持原有DMXAPI域名及Anthropic官方`https://api.anthropic.com`。Anthropic地址可配置服务根地址、
  `/v1`或`/v1/messages`，不会追加出`/v1/v1/messages`。
- 兼容 `ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`、`ANTHROPIC_AUTH_TOKEN`／`ANTHROPIC_API_KEY`
  环境变量；显式的 `MODEL_*` 优先。显式空的`MODEL_API_KEY`不会从其他变量恢复密钥。
  不会自动读取Claude Code的凭据文件，也不能据命令行成功就假设两边一定是同一令牌。
- 切回OpenAI格式：`MODEL_API_FORMAT=openai`、`MODEL_AUTH_MODE=bearer`；原`/chat/completions`
  请求与SSE处理保留。单文件数据和图片模式都可走任一协议，但模型自身是否支持图片需服务端确认。

适配依据：[Messages接口](https://platform.claude.com/docs/en/api/messages/create)、
[流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。
示例文件只应放占位符；如果之前曾把真实密钥放入示例或提交仓库，建议在服务商处轮换。

- MODEL_API_KEY：百炼API Key，真实调用必填。
- MODEL_NAME：默认模型名称，可选。
- MODEL_BASE_URL：默认接口地址，可选。

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

智能体真实调用示例（服务端已经配置MODEL_API_KEY）：

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
python -B -m unittest discover -s backend/tests -t . -v
node frontend/tests/test_frontend_stream.cjs
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
MODEL_API_KEY=
MODEL_NAME=qwen3-vl-plus
MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

仅在本机.env的MODEL_API_KEY等号后填写真实密钥，示例文件不得放真实密钥。
.env及其变体已被Git忽略，.env.example可提交。不要截图或分享填好后的.env。
若系统已配置同名环境变量，其值优先，.env不会覆盖（包括系统中的空值）。
网页与识别API不再接受model、base_url、api_key或X-Dashscope-Key覆盖，旧调用方式返回400，智能体只需上传图片及任务字段。
/api/health只返回key_configured布尔状态，不返回模型名称、接口地址或密钥。
回复/请求元数据可能仍含实际调用的模型与接口记录，这是实验结果追溯，不是前端配置入口；密钥不会写入这些结果文件。
CLI保留显式参数用于本机开发；旧desktop_app.py保留兼容，不是当前网页入口。


## DMXAPI第三方兼容服务

本机.env可设置MODEL_BASE_URL=https://www.dmxapi.cn/v1；程序自动请求/v1/chat/completions。
这不是智谱或阿里云官方域名；使用时图片、平台参数、提示词和认证密钥会发送给DMXAPI服务。
MODEL_API_KEY必须填写DMXAPI签发的密钥，不是百炼密钥。
MODEL_NAME须为DMXAPI实际支持且可接收图片的模型ID；供应商显示名Zhipu GLM不是模型ID。
本次只修改配置与兼容路径，没有向该服务发送真实请求，也未验证其模型可用性。


## 实时运行日志

页面右侧新增“运行日志”，按请求显示上传接收、参数检查、密钥是否配置、图片数量/大小、模型等待、轨迹解析、绘图与错误阶段。
日志不会记录密钥、图片Base64、文件内容、完整提示词或模型回复原文；错误文本会对当前密钥和常见密钥形式脱敏。
前端通过X-Request-ID（UUID）关联请求，GET /api/logs/{request_id}?after=<seq>增量轮询；同编号不能重复提交。
GET /api/logs/{request_id}/download下载本次文本日志。清空显示不删除后端日志。
日志只保存在内存，最多128个请求、每请求200条，重启后清空；不是完整持久化审计系统。
本地服务没有用户登录隔离，拥有本机访问权限且知道请求ID者可查看对应日志。请勿暴露到公网。
日志显示“调用模型”只代表已开始请求；HTTP200不保证成功绘制轨迹，应同时查看轨迹状态和WARN。


## 本地轨迹图相似度对比

页面底部“04 轨迹图对比”：先生成AI位置轨迹，再选择同一样本truth/btr_A_track.png，点击计算。
两张图并排显示，并显示归一化彩色轮廓相似度（0～100）、AI轮廓匹配率和参考轮廓覆盖率；可查看青/粉/白叠加图及下载JSON报告。
参考图仅提交到本地/api/compare，代码不会调用send_images或将其加入模型输入；相似度由本地算法计算，不由大模型自评分。

算法：提取高饱和彩色前景，尽量去除黑色背景与灰色文字/坐标；按前景包围盒分别缩放为256×256，再以3像素（方形邻域）容差计算双向覆盖率的调和平均。
该处理忽略绝对位置、尺度及纵横比例差异，不评测米级误差、目标编号对应、时间或运动方向。图例、标记、线宽、裁剪会干扰。
仅支持黑底彩色轨迹图，不保证适用于灰度图、热力图或复杂截图；比较前务必人工确认同一样本。
这不是定位准确率、置信度或物理真实性评估；形状相近的错误地理轨迹仍可能得到高分。真正位置评测应另用truth.jsonl与tracks.json作时空匹配。
AI仅输出bearing_time时拒绝与空间轨迹图评分；没有AI位置图也不评分。刷新页面后须重新生成/选择本次AI结果，暂不提供历史结果选择器。

POST /api/compare：multipart字段result_id（已生成的AI结果编号）、reference（参考轨迹图≤10MiB）。
返回similarity_percent、ai_outline_match_percent、reference_coverage_percent、warnings及叠加图和报告链接。
输出保存到outputs/<result_id>/comparisons/<comparison_id>/，含comparison_overlay.png、report.json；多次比较不会覆盖前次结果，不保存参考原图。
GET /api/comparisons/{result_id}/{comparison_id}/{filename}查看或下载本地结果。


## 结构化评测（替代只看图形分数的局限）

页面“轨迹图对比”下面新增“结构化评测 · 真值核对”。不需要参考图片：
- 上传同一样本semantic.json：按选定平台targets清单核对AI独立目标ID数量，显示计数绝对误差。场景字段仅供参考，当前AI轨迹响应没有六项场景预测，不能伪称六项已评测。
- 上传同平台truth/btr_A_truth.jsonl：计算真实空间位置误差；可只提供此文件，目标数量从时序真值ID去重得到。
- 两者一起上传会检查参考目标清单一致性；多平台真值须拆为单个平台。

AI轨迹不按T1/T2编号直接对齐：在共同时间点计算各候选轨迹对的位置RMSE，按门限过滤后做全局一对一最小代价匹配（优先匹配数量，再降低误差）。
默认匹配门限1000米，可修改；超出门限的对象列为未匹配，绝不会静默删除后声称准确。
输出每目标/汇总RMSE、平均误差、P95误差、参考时刻覆盖率，以及双方未匹配清单。
聚合误差只覆盖匹配的可评测点，不惩罚未匹配点；必须结合漏检/多检与总体覆盖率解读，不能只挑RMSE。
预测精确时间点直接评测；相邻点间隔≤最大插值间隔（默认10秒）才线性插值，超过时仅评测实际给出的点。不外推、不跨断续分段补线。

local_xy_m转换使用生成时保存在tracks.json的coordinate_reference（首平台原点，东/北方向米，球半径6371000米）。
新生成结果会自动保存参考；旧结果缺原点时只给计数，不猜原点，需重新生成。lon_lat直接使用经纬度，位置误差采用球面大圆距离（米），不做图形缩放平移对齐。
若AI输出bearing_time则只核对计数，不计算空间误差。若同一ID有多个候选分支，暂不按真值择优，须先消歧后评测。

POST /api/evaluate：multipart字段result_id、semantic（可选≤2MiB）、truth（可选≤16MiB）、gate_m（默认1000）、max_gap_s（默认10）。参考至少一项必填，最大12个真值目标。
本地保存outputs/<result_id>/comparisons/<evaluation_id>/evaluation.json，支持报告下载。
上传的参考不写入模型请求；也不利用真值修改或重新生成AI轨迹。框内图片形状分数与本结构化指标分开，不合成含义不明的“准确率”。
用户必须确认参考文件与生成输入同源；当前只能检查平台/目标清单等有限一致性，不能自动证明所有文件来自同次实验。


## 通用中转配置名称

.env文件名不变，连接字段统一为MODEL_API_KEY、MODEL_NAME、MODEL_BASE_URL。现有值迁移保留；.env.example的密钥必须为空。
进程环境中的通用名称优先于.env相同字段。为兼容旧部署，代码仍支持旧DASHSCOPE_API_KEY/QWEN_MODEL/QWEN_BASE_URL作为通用变量不存在时的回退；新变量明确为空时不恢复旧密钥。
403与变量名称无关：需由中转站确认密钥权限、额度及准确的视觉模型ID。Zhipu GLM不是已经核实的API模型ID，工具不会擅自替换成猜测名称。


## 长请求与等待（最新设置）

模型请求不再设置本地等待时限，urllib显式传入timeout=None，网页也不会按120/300秒中止识别。
.env中旧MODEL_TIMEOUT_SECONDS已移除，进程内遗留的同名变量也不再生效。
等待期间每15秒记录“仍在等待”，不代表模型生成进度，也不会触发自动重试。
中转站、模型服务、系统网络或反向代理仍可能自行超时或断开，本工具无法取消这些外部限制。
连接中断与上游超时仍显示明确错误；关闭网页不能撤回已发送请求，可能仍会计费。
无响应的请求可能持续占用处理槽位（同进程最多两个），不要重复点击发送。
CLI和旧桌面入口默认同样不设本地等待时限；调用client.send_images的开发者仍可显式传入超时用于独立测试。
更改代码后请停止旧服务并重新启动，不能只刷新网页。


## 真正的模型流式输出（最新）

网页真实识别改为POST /api/recognize/stream，向中转站发出stream=true请求；不再等待整包回复后假装逐字显示。
使用HTTPX异步读取SSE，浏览器fetch流式解析。旧POST /api/recognize保留给兼容调用和预检。
同样的multipart字段files/prompt/consent/platform；连接配置仍只读.env，模型请求timeout=None，不设置本地等待时限。
阶段：本地accepted → connecting等待上游响应头 → headers HTTP状态 → connected SSE已建立 → delta正文/reasoning服务端推理文本 → rendering → result最终结果。
等待时每15秒发heartbeat，明确不是生成进度。事件包含event字段，外层SSE也有event名称；data是单个JSON对象。
若中转站缓冲或模型自身没有输出，客户端无法凭空显示文本；连接阶段能帮助区分卡在响应头前还是首片段前。

正文与上游reasoning_content分别显示，推理文本不是本工具的内部思考，也不是已验证结论。为保证显示性能正文预览最多400万字符、推理预览最多100万字符，完整结果可下载。
上游返回application/json而非text/event-stream时明确显示buffered提示，只能一次展示整包，不伪称实时流。
检测到无结束标记的断流时不生成成功结果；显示的片段可能不完整。上游finish_reason非stop会在结果中提示可能截断。
中转站错误仅提取限长、脱敏后的error.code/message，不显示原始响应体、认证头或图片Base64。

模型正文完整后保存answer.txt、组装response.json及stream_state.json；有推理文本时另保存provider_reasoning.txt。
轨迹绘制、自动显示、图片对比及结构化评测继续使用最终tracks.json，不对半截JSON绘图。
失败时尽可能保存answer.partial.txt与失败状态，不能将其视为完整结果。停止本地接收会取消本地网络读取；不保证上游取消处理或费用。
流式HTTP响应头发出后不能改状态码；因此HTTP200仅表示SSE连接建立，真正成功看result事件，失败看error事件。

服务端使用有界事件队列和两个处理槽位，断开后清理临时上传与释放槽位；没有自动重试、隐式重新发送或付费连通性测试。
页面已改成流式按钮，旧成功日志轮询不再用于真实识别；阶段日志仍可通过原日志接口下载。
重新启动后端并刷新网页才能启用。若前面还有Nginx等反向代理，需要关闭SSE缓冲并调整代理自身超时；本应用返回X-Accel-Buffering:no，不能保证第三方中转也遵守。


## GLM长推理与中文正文

当前.env增加MODEL_REASONING_EFFORT=low，build_request将其作为reasoning_effort透传至服务端。该参数影响推理强度，不是超时或token截断，仍然不设置本地等待时限。
GLM-5.3/Flash官方文档说明不支持thinking.type=disabled，支持low/high/max；因此本工具没有盲目发送disabled。
中转站的自定义模型别名是否支持/遵守此字段尚未验证，不保证立即结束思考；更换不支持此参数的模型时应将配置留空。
默认提示词要求最终JSON说明使用简体中文、信息不足立即降级bearing_time；模型的reasoning_content语言由上游决定，提示词不能保证推理文本为中文。
仅重启后的新请求采用这些配置。正在运行的请求无法事后修改推理强度或提示词，停止本地接收也不保证撤销云端计费。
官方依据：https://docs.bigmodel.cn/cn/guide/capabilities/thinking


## 生成图自动导入与历史结果

AI返回estimated_track.png后，无论是位置轨迹还是bearing_time，都会自动显示在对比区，方位—时间图显著标注“非地理轨迹”。
为避免刷新后丢失已付费生成的结果，新增GET /api/history（最近30个本地有图结果）与GET /api/history/{result_id}。
对比框选择已有结果，点击“导入已生成轨迹图”，直接恢复生成图、正文与轨迹类型，不重新调用模型。
参考图新增reference_kind：position（默认）或bearing_time。后端按真实tracks.json核对AI坐标类型，不接受不同类型混评。
方位—时间图可以与同一样本的方位—时间轨迹图做视觉轮廓比较；不能使用声呐热力图或地理轨迹当参考。
类型选择由用户声明，不能自动证明参考图的坐标类型和样本来源；仍需人工核对。
这只放开同类型图形展示与轮廓评分，不代表恢复了地理位置；结构化评测中的bearing_time仍不计算空间位置误差。

数量与节点过程实现边界见 [说明](docs/target-count-progress.md)。

跨时段ID合并、串线隔离及无费用本地重处理见 [身份复核说明](docs/identity-association.md)。
