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

默认保留原 Windows 脚本的完整流程：

1. 第一轮：聊天 → 固定 JSON → 散文 → 16K 输入。
2. 第二轮：同样顺序。
3. 连续两次 asyncio 代码生成。

聊天、散文和代码最多 1200 tokens，temperature 为 0.6；JSON 最多 8192
tokens、temperature 为 0；16K 输入生成 32 tokens。全部关闭 thinking，
top_p=0.95、top_k=20，其余参数和原脚本一致。
两轮均计入结果，不会偷偷丢弃首轮。`--dry-run` 可离线查看请求计划。

```powershell
local-llm-bench run --base-url http://127.0.0.1:18235 --output results/build-A.json --label build-A --notes "NVFP4; 5090 text; 5060 Ti vision; spec=7; KV=int8"
local-llm-bench compare results/build-A.json results/build-B.json
```

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

默认固定协议是 `local-chat-v1`。修改提示、顺序、采样或长度时必须升级协议版本，
不能悄悄改变默认测试。详见 [完整协议](docs/protocol.md)。
