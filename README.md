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
