# local-llm-bench

用于 **Basalt 和 Strata** 的统一 API 测速工具，支持 Windows 和 Linux。
只需 Python 3.10+，没有运行时第三方依赖，不需要指定模型文件、源码或缓存目录。

## 安装与测速

```powershell
git clone https://github.com/LesterLyu/local-llm-bench.git
cd local-llm-bench
python -m pip install .

# 按平时的方式启动模型，然后运行对应服务的地址
local-llm-bench run --base-url http://127.0.0.1:18235 --label basalt-build-A
local-llm-bench run --base-url http://127.0.0.1:18234 --label strata-build-A
```

如果命令不在 PATH 中，可用 `python -m local_llm_bench` 代替。
两种服务使用相同的提示、采样参数和顺序；模型别名从服务自动读取。
工具不会启动、关闭、卸载模型，也不会修改服务配置。

测速成功后自动保存 JSON 和同名 `.md` 报告。例如 `--output results/build-A.json`
会同时生成 `results/build-A.md`。报告默认把全部测试整合成一张表，包含每轮速度、
原测试均值、扩展测试中位数、首 token 延迟、草稿接受率和 Agent 缓存指标。
加 `--lang zh` 使用中文标签；加 `--layout detailed` 可查看原来的英文详细布局。
这两个选项只影响报告，不改变测试请求。

**默认就是全部跑**，顺序为：

1. 第一轮：聊天 → 固定 JSON → 散文 → 16K 输入。
2. 第二轮：同样顺序。
3. 连续两次 asyncio 代码生成。
4. 扩展套件三轮：流式短对话、4K / 16K / 32K 输入、Agent 多轮缓存。

上下文足够时，共 48 个请求：原测试 10 个，扩展部分两次校准加 36 次测量。
一份 JSON 保存两套各自的完整结果；原协议和历史比较方式保留不变。

聊天、散文和代码最多 1200 tokens，temperature 为 0.6；JSON 最多 8192
tokens、temperature 为 0；16K 输入生成 32 tokens。全部关闭 thinking，
top_p=0.95、top_k=20，其余参数和原脚本一致。
两轮均计入结果，不会偷偷丢弃首轮。`--dry-run` 可离线查看请求计划。

```powershell
local-llm-bench run --base-url http://127.0.0.1:18235 --output results/build-A.json --label build-A --notes "NVFP4; 5090 text; 5060 Ti vision; spec=7; KV=int8"
local-llm-bench compare results/build-A.json results/build-B.json
```

已有 JSON 可以离线生成同样的表格，无需重新调用模型：

```powershell
local-llm-bench report results/build-A.json --lang zh --output results/build-A-zh.md
```

草稿接受率按接受总数 / 提供总数加权，可能包括 MTP 和后缀匹配草稿。
缺失的引擎速度保持空值，校准只列单次结果、不计性能汇总。
报告文件不覆盖；若同名 Markdown 已存在，会在发出测速请求前报错。

需要 API key 时设置环境变量 `LOCAL_LLM_BENCH_API_KEY`，或用
`--api-key-env NAME` 指定其他变量；密钥不会写入报告。

## 如何读结果

- 输出和预填充速度取自服务响应的 `timings`，不会用 HTTP 总耗时冒充解码速度。
- HTTP 端到端速度单独保存为 `end_to_end_tok_s`。没有引擎计时时明确标为缺失。
- `--engine-log 路径` 可按原 Windows 脚本方式解析本次请求的引擎日志；一般使用者不需要。
- 本地运行时自动采集实际 GPU 功率上限、利用率、显存和时钟。显卡可见不代表它参与文本推理，分工请写进 `--notes`。
- JSON 测试必须返回 200 项数组。Qwen 参考 token 数默认只警告差异，可用 `--strict-tokens` 严格检查。
- 16K 预填充要求缓存命中为零；若提示被复用，请先在服务中清理提示缓存，或使用刚加载的服务。
- 有其他请求或模型未加载时拒绝测试。失败或中断会保留不完整结果，不覆盖已有文件。
- 支持读取之前 `live-speed.py` 的旧结果；报告会明确说明旧文件没有协议指纹。

对照时应保持模型量化、上下文、MTP、专家缓存、显卡分工和功率上限一致。
比较版本时用同一环境交替跑 A/B/A/B，分别看首轮和第二轮；不要将不同任务或
不同配置下的一次结果直接当作版本提升百分比。

默认组合协议是 `all-v1`。其中原 Windows 子套件仍为 `local-chat-v1`，
提示、顺序、采样和长度保持不变。详见 [默认全量协议](docs/all.md) 与
[原协议](docs/protocol.md)。只跑原测试可指定 `--suite local-chat-v1`；
只跑扩展可指定 `--suite serving-v1`，并用 `--long` 加上 32K。
默认全量无需指定这些选项，自动包括 32K。

## 从自带 bench 整合的扩展套件

```powershell
local-llm-bench run --suite serving-v1 --base-url http://127.0.0.1:18235 --output results/serving-A.json
local-llm-bench run --suite serving-v1 --base-url http://127.0.0.1:18234 --output results/serving-B.json
local-llm-bench report results/serving-A.json --output results/serving-A.md
local-llm-bench compare results/serving-A.json results/serving-B.json
```

`serving-v1` 借鉴 Strata 社区 bench，默认每项三次，可用 `--rounds 1` 做冒烟测试：

- 流式短对话：客户端首 token 延迟（TTFT）和总耗时、引擎解码速度。
- 新鲜的 4K / 16K 输入，`--long` 加测 32K；记录实际 token 数和预填充速度。
- Agent 会话：四个工具定义，初始回复后追加五段固定文档，保留模型之前的回答；
  测缓存复用比例、旧上下文重读量和逐轮 TTFT，不执行工具。
- 输出中位数及最小/最大值；保留所有重复结果，引擎计时缺失时保持空值。
- 两个套件都提取 API 返回的 draft offered / accepted 计数和接受率，不根据速度猜测。
- 离线生成 Markdown 报告，默认不带地址、原始状态、请求正文、自由备注等诊断信息。

开始时有两次单 token 长度校准请求，完整保存在 JSON 的 `calibration` 阶段，
不纳入测速统计；没有额外预热。长输入使用新的 UUID 前缀，命中提示缓存则中止，
避免把缓存尾段当成完整预填充。Agent 文档来自随包固定内容，不读取你的仓库。
上下文不够的测试会明确跳过。测速时仍是一条请求接一条，不启动或修改服务。

这是适配后的统一协议，与 Strata 自带 bench 的预热、文档选择方式不完全相同；
也不能与 Basalt 原生 `basalt-bench` 或原 `local-chat-v1` 混比。
对比时必须使用相同套件、轮数、`--long` 选项和测试范围。
Markdown 中的模型名与标签、JSON 中的诊断信息，分享前仍应检查。
详见 [扩展协议](docs/serving.md)。

组合报告之间对比时分别展示原测试和扩展测试。组合报告与旧 Windows 报告对比时，
会明确说明只对照匹配的原测试子套件。`--rounds N` 会覆盖所选套件的轮数，
因此默认全量的 `--rounds 1` 是 19 个请求，适合快速验证。
