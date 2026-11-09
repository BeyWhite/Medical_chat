# 「问答改」工作流改造说明

> 目标应用：Dify 里的 **问答改**（app_id `ce52509d-df51-4bfb-961d-5f6c9737dab4`，模式 advanced-chat 对话流）
> 本目录里是**改造用的全部工具、每一步的快照、以及回退方法**。

---

## 一、改造前 → 改造后

**改造前**（原始流程，4 个节点）：

```
开始 → 知识检索 → LLM → 直接回复
```

**改造后**（17 个节点）：

```
                                                   ┌─(分类=医学知识文件)─┐
                                                   │                     ▼
开始 ─────► 问题分类器 ───────────────────► 文档提取器 ──► 大模型:生成问答对 ──► 代码:取文件名
（上传文件进 sys.files）  │                                                          │
                          │                                                          ▼
                          │                                             HTTP:按文件名新建知识库
                          │                                                          │
                          │                                                          ▼
                          │                                                  代码:取知识库ID
                          │                                                          │
                          │                                                          ▼
                          │                                          HTTP:写入问答对 txt ──► 直接回复(文件分支)
                          │
                          └─(分类=医学问题)─┐
                                            ▼
                             大模型:生成五个相似问 ──► 代码:解析相似问数组
                                                          │
                                                          ▼
                                            ┌───── 迭代(循环检索知识库回答) ─────┐
                                            │ 迭代开始 → 知识检索 → 大模型:逐问作答 │
                                            └────────────────┬───────────────────┘
                                                             ▼
                                              大模型:去重汇总作答 ──► 直接回复
```

---

## 二、每一步改了什么（可单独回退到任意一步）

| 步骤 | 内容 | 新增/改动的节点 |
|---|---|---|
| **step0** | 原始流程（改造前的样子） | — |
| **step1** | 开始节点增加文件变量 `knowledge_file`，并开启应用的文件上传 | 开始 |
| **step2** | 开始节点后插入**问题分类器**（医学知识文件 / 医学问题） | 问题分类器 |
| **step3** | 文件分支：文档提取器 → 大模型生成问答对（JSONL 格式） | 文档提取器、生成问答对 |
| **step4** | 文件分支：按**文件名新建知识库** → 写入问答对 txt | 取文件名、新建知识库、取知识库ID、写入问答对txt、文件分支-回复 |
| **step5** | 问题分支：大模型生成**五个相似问** → 代码节点解析成数组 | 生成五个相似问、解析相似问 |
| **step6** | 问题分支：**迭代循环**（知识检索 → 逐问作答），原「知识检索」「LLM」搬进迭代体 | 循环检索知识库回答、迭代开始 |
| **step7** | 问题分支：大模型**去重汇总** → 直接回复 | 去重汇总作答 |
| **step8** | **修复版**：文件来源兼容（对话附件 / 开始节点文件变量）+ 正文有效性校验（提取失败不再生成假问答对）+ 同名知识库自动复用（不再 409）（**当前已应用**） | 取文件名/判断来源、判断文件来源、是否有对话附件、文档提取器×2、正文汇总、校验正文、正文是否有效、提示-未能提取正文、生成问答对、查知识库列表、查找同名知识库、知识库是否已存在、确定知识库ID |

每一步的完整图 JSON 都在 `steps/stepN.json`，可随时审阅或回退。

---

## 三、工具用法

在本目录下执行（只用标准库 + 本机 Docker，无需装任何包）：

```bash
python wf_build.py                     # 重新生成 steps/step0..8.json
python wf_deploy.py status             # 看当前草稿状态（节点构成 / 已发布版本数）
python wf_deploy.py snapshots          # 列出所有可回退的快照
python wf_deploy.py apply 4            # 应用第 4 步（写入 Dify 草稿，自动先备份）
python wf_deploy.py apply 7 --publish  # 应用第 7 步并发布
python wf_deploy.py rollback 3         # 回退到第 3 步
python wf_deploy.py rollback 20261010-162300_before_step8 --publish   # 回退到某次自动快照
python wf_deploy.py sync-datasets --once    # 把新知识库并入检索节点并重新发布
python wf_deploy.py sync-datasets --watch   # 常驻监听，自动同步（见下）
```

**回退保障**：每次 `apply` / `rollback` 都会先把"当前草稿"存成一份带时间戳的快照放进 `backup/`，
所以**回退动作本身也可以再回退**。改造前的原始流程也单独留了一份：
`backup/wf_ce52509d_original_graph.json`。

---

## 四、为什么需要「sync-datasets」这一步

Dify 的**知识检索节点里勾选的知识库是静态的**（写死在图里），运行时新建的知识库不会自动出现在里面。
按你的要求"按文件名新建知识库、下次提问能被检索到"，本目录提供了同步工具：

- **一次性**：`python wf_deploy.py sync-datasets --once`
- **常驻自动**（推荐）：双击 `start-kb-sync.bat`，它会每 8 秒检查一次，
  发现新知识库就自动并进检索节点并重新发布；`stop-kb-sync.bat` 停止。

> 注意：重新发布会生成一个新的应用版本。若你正在 Dify 页面里编辑，建议先保存再同步。

---

## 五、必要的服务端配置（已为你做好）

Dify 的 **HTTP 请求节点走 SSRF 代理**，默认**禁止访问内网地址**，因此"写入知识库"这一步会被拦截。
已在 `D:\software\bag\dify-1.17.1\dify-1.17.1\docker\.env` 增加：

```
SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=api
```

并重建了 `ssrf_proxy` 容器（只放行 `api` 这一个内网域名，最小授权）。

**撤销方法**：删掉 `.env` 里这一行（备份在 `.env.bak-before-kb-allow`），然后
```
cd D:\software\bag\dify-1.17.1\dify-1.17.1\docker
docker compose up -d --force-recreate --no-deps ssrf_proxy
```

---

## 六、关键参数（想改的话改这里）

| 参数 | 位置 | 当前值 |
|---|---|---|
| 知识库 API 密钥（写入用） | `wf_build.py` → `DATASET_API_KEY` | `dataset-REPLACE_WITH_YOUR_KEY` |
| 内网 API 地址 | `wf_build.py` → `API_BASE` | `http://api:5001/v1` |
| 向量模型（新建知识库用） | `wf_build.py` → `EMBEDDING_MODEL` / `EMBEDDING_PROVIDER` | `Pro/BAAI/bge-m3` / siliconflow |
| 大模型 | `wf_build.py` → `LLM_PROVIDER` / `LLM_MODEL` | siliconflow `deepseek-ai/DeepSeek-V3.2` |
| 知识库列表（检索用） | `wf_build.py` → `DATASET_IDS` | 原应用的 9 个知识库 |

改完跑 `python wf_build.py && python wf_deploy.py apply 8 --publish` 即可生效。

---

## 七、测试记录（实测已通过）

测试用的临时应用密钥已删除，测试新建的知识库已清理；原始证据留档在本目录 `backup/`。

**1）问答分支**（`backup/test_question.json`，问题「什么是尿路刺激征？」）

| 节点 | 状态 | 耗时 |
|---|---|---|
| 问题分类器 | succeeded | 9.7s |
| 生成五个相似问 | succeeded | 4.9s |
| 解析相似问 | succeeded | 0.1s |
| 循环检索知识库回答（迭代） | succeeded | 165.6s |
| ├ 知识检索 → 逐问作答 ×5 轮 | succeeded | 每轮 8~32s |
| 去重汇总作答 | succeeded | 25.0s |
| 直接回复 | succeeded | — |

整体约 **3 分 28 秒**，回答正确并带知识来源（命中 3 条，最高相关度 0.998）。

**2）文件分支**（`backup/test_file.json`，上传 `sample_medical.txt`：中暑现场急救）

| 节点 | 状态 |
|---|---|
| 问题分类器 → 文档提取器 → 生成问答对 → 取文件名并转义 | succeeded |
| 新建知识库（按文件名） | succeeded |
| 写入问答对 txt | succeeded（写入 1988 字节，文档状态 `completed`） |

回答：`✅ 已处理文件《sample_medical.txt》…已按文件名新建知识库…已将生成的问答对 txt 写入该知识库`

**3）"下次提问能检索到新知识库"**（`backup/test_recall.json`）

跑 `sync-datasets` 把新知识库并入检索节点后，问「重症中暑现场应该怎么急救？」，
命中的第 1 条就是新知识库：

```
命中片段数: 3
 - sample_medical.txt | sample_medical.txt | score 0.985   ← 新建的知识库
 - 急救1.txt...        | 急救1.txt          | score 0.940
 - 急救1.txt...        | 急救1.txt          | score 0.718
```

**4）step8 修复验证（已实测）**

- **没传文件**（问「帮我整理一份医学知识文件」）→ 直接返回提示
  「没有收到文件内容。请在对话框的附件按钮上传文件，或在开始节点的「医学知识文件」里选择文件后重试。」
  （以前会拿默认名「医学知识文件」生成空问答对，并引发后续 409）
- **对话附件上传** `sample_medical.txt` → 建库成功，返回
  `✅ 已处理文件《sample_medical.txt》…（新建知识库）…知识库 ID：6b225799…`
- **同一文件再传一次** → 返回 `（复用已存在的知识库）`，**不再 409**
- **问题分支** 回归测试「什么是尿路刺激征？」→ 分类器 3.9s → 生成 5 相似问 3.2s →
  迭代 5 轮检索+作答 113.3s → 去重汇总 12.9s，回答准确且有条理。

> 踩坑记录：step8 第一版「新建知识库」节点的 JSON 体里引用了 **step4 才有的节点 id**
> （`node_code_filename`），导致占位符没被替换，Dify 真的建了一个名字叫
> `node_code_filename.filename_esc` 的知识库，第二次跑就 409。
> 已改为引用当前的 `node_code_filesrc`，并加了一条"全图校验不存在悬空引用"的检查。

> 踩坑记录 2：开始节点的文件变量最初是单值类型 `file`。Dify 的代码节点只把
> **`array[file]`** 转成 dict 列表，单个 `file` 会以 `File` 对象传入，导致
> `Type is not JSON serializable: File`。已把变量类型改为 **`file-list`**
> （对应 `array[file]`），代码节点与文档提取器都能正常处理。

**5）step8 第二轮修复验证**

- 把文件放进**开始节点变量**（`inputs.knowledge_file` 传数组）→ 建库成功、
  写入 2212B，错误消失。
- 对话附件路径 → 建库成功；同文件再传 → 「复用已存在的知识库」。
- 无文件 → 明确提示。

**6）「知识库内容不对：文档内容为空」修复（重要）**

现象：某 `.docx` 入库后，知识库里只有一条
`{"question": "文档内容是什么？", "response_chosen": "文档内容为空。"}`。

根因链：

1. 该 DOCX 内部 ZIP 条目（`word/media/image1.jpeg`）的 **CRC 被写成 0**，
   与实际数据不符 → python-docx 抛
   `Failed to extract text from DOCX: Bad CRC-32 ...`；
2. 文档提取器配了 `error_strategy=default-value`，失败后**静默返回空文本**；
3. 大模型拿到空内容，只能编出"文档内容为空"。

修复分两层：

- **流程层**（step8，写入 `wf_build.py`）：新增 `正文汇总`(variable-aggregator)
  → `校验正文`(code) → `正文是否有效`(if-else) → 无效则走
  `提示-未能提取正文`，**不再把空内容喂给大模型**。
  > 注意 `variable-aggregator` 的输出变量名是 **`output`**（不是 `text`），
  > 且输入是 `array[string]` 时要把 `output_type` 也写成 `array[string]`。
- **平台层**（补丁 `patch_docx_crc.py`）：让 Dify 的 DOCX 提取**跳过损坏的 CRC 校验**。
  关键点：Python 3.12 的 `zipfile` 在 `ZipFile.open()` 时把 `ZipInfo.CRC` 快照到
  `ZipExtFile._expected_crc`，**只有该属性为 None 才跳过校验**，
  所以必须覆盖 `open()` 去置空它（只改 `ZipInfo.CRC` 反而会变成"期望 0、实际非 0"）。
  补丁落在容器内，并已通过 `docker-compose.yaml` 的**只读挂载**持久化
  （`docker/volumes/patches/`），重建容器也不会丢。

效果：同一个 `.docx` 现在提取出 **2000+ 字正文**，生成 60+ 条高质量问答对，
入库文档 `word_count=4999`（修复前是 1 条空内容）。

---

## 八、已知限制

1. **检索节点数据集是静态的** —— 新建的知识库需要 `sync-datasets` 并入（见第四节）。
2. **文件入口有两个，都已支持**（step8 起）——对话框附件走系统变量 `sys.files`；
   开始节点表单里的「医学知识文件」变量走 `knowledge_file`。两个都没有时会给出提示。
3. **相似问是 5 个**，会带来 5 次知识库检索 + 5 次大模型调用，回答整体延迟较高（分钟级）。
   想更快可以把生成数量改成 3 个（改 `wf_build.py` 里的提示词）。
4. 生成的知识库会**按上传文件名命名**；step8 起同名文件重复上传会**复用已有知识库**（不再 409），
   并把新的问答对追加为同一知识库下的新文档。
5. **DOCX 的 CRC 补丁是针对镜像内部文件的**，Dify 镜像升级后需重新打
   （见 `docker/volumes/patches/README.md`）。

---

## 九、Dify 里的版本历史

本次改造在 Dify 的版本历史里留了几条发布记录（`step7` / `step7 修正` / `auto-sync-datasets` 等），
那是我调试过程中的中间发布版本，**你可以在"版本历史"里直接删掉不需要的**，
只留最后一条即可（当前生效的就是最后一条）。

若想把"每一步改动"也做成 Dify 里可见的版本，可以按顺序发布：

```bash
for i in 1 2 3 4 5 6 7; do python wf_deploy.py apply $i --publish; done
```

---

## 十、另一个应用：问答3.0（写入知识库配置）

应用 **问答3.0**（app_id `37d7e389-1265-456d-a102-94ecaa2f242a`）结构与本目录的「问答改」step8 一致，
但**写入知识库走 HTTP 节点**（对话流里 Dify 没有可用的"知识库写入"节点，见下）。
本目录另配了两个工具：

```bash
python wf30_patch.py check                  # 只读查看写库节点 / 检索节点当前配置
python wf30_patch.py backup                 # 备份草稿
python wf30_patch.py apply --publish        # 按规格改配置并发布
python wf30_patch.py rollback <快照名>       # 回退（备份在 backup/wf30_*.json）

python verify_kb_rule.py                    # 端到端实测：建库 -> 回读 -> 写文档 -> 看分段 -> 删库
```

**写库节点采用的规格**（`wf30_patch.py` 里的常量，想改改这里）：

| 项 | 值 |
|---|---|
| 知识库类型 | 即用型（普通）知识库，`POST /v1/datasets` |
| 分段模式 | `custom`：分隔符 `\n\n`、最大 1024、重叠 50 |
| 文本预处理 | `remove_extra_spaces` = 开（替换连续空格/换行/制表符）|
| 索引方式 | `high_quality` |
| Embedding | siliconflow `Pro/BAAI/bge-m3` |
| 检索 | `semantic_search`（向量检索）+ Rerank `BAAI/bge-reranker-v2-m3`，TopK 3，阈值 0.5 |

**两个容易踩的坑**（已实测确认）：

1. `POST /v1/datasets` **不接受** `process_rule`（传了不落盘，`dataset_process_rules` 不会有行）。
   真正生效的是**建文档时**的 `process_rule`，也就是「写入问答对txt」节点那一步。
   所以两个节点都写了同一套规则：建库那处只作声明，写文档那处才是实际生效点。
2. `mode: "automatic"` **不等于**上面的参数：automatic 是 `分隔符 \n / 500 tokens`；
   后端文档兜底默认是 `\n / 1024`；而**前端"自定义分段"的默认值**才是
   `\n\n / 1024 / 50 + 替换连续空格换行制表符`。要得到这套必须显式用 `custom`。

> 关于"Dify 的知识库写入节点"：`knowledge-index`（前端目录 `nodes/knowledge-base`）在后端
> `KnowledgeIndexNode._run()` 里只从**系统变量** `sys.dataset_id` / `sys.document_id` / `sys.batch`
> 取目标，自己不建库，属于 **RAG 流水线（数据源）** 场景的节点。
> 普通对话流里没有它的位置，因此本应用的"写入知识库"是 HTTP 节点实现的。

---

## 十一、应用「问答4.0」：每个 JSON 语句 = 一个分段

应用 **问答4.0**（app_id `caecf6d8-5f31-4c24-b426-ea84d4c7bb02`，advanced-chat，结构同「问答改」step8）。
工具：`wf40_patch.py`（check / backup / apply [--publish] / rollback / publish）。

```bash
python wf40_patch.py check                 # 只读看写库节点现状 + 规格自检
python wf40_patch.py verify                # 只跑规格自检（不通过时退出码 1，可脚本调用）
python wf40_patch.py apply --publish       # 改草稿并发布（自动先备份）
python wf40_patch.py rollback wf40_20261011-111344_before_patch   # 回退
python test_qa40.py                        # 端到端实测（自建临时密钥+临时知识库，测完清理）
```

> ⚠️ **改完必须让 Dify 页面"退出去再进来"**
> Dify 的工作流编辑器是**整图提交**的：如果页面里开着**改造前**的工作流没刷新，
> 编辑器自动保存（或你点保存/发布）会把浏览器内存里的**旧图整份写回草稿**，
> 这次改动就被覆盖了 —— 实测发生过：11:15 打好补丁，11:29 你用那个旧页面跑测试，
> 走的还是 `automatic`（分隔符 `\n` / 500 / 重叠 50 → 每段正好约 500 字），
> 11:30 编辑器又把旧图写回了草稿。
>
> **正确姿势**：改完后在 Dify 里按 **F5 刷新**（或关掉重开）再操作；`check/verify` 随时可确认。


**改了什么（2 处）**

| 节点 | 改动 |
|---|---|
| 写入问答对txt (`node_http_add_doc`) | `process_rule` 由 `automatic` 改成 `custom`：**分隔符 `\n`、最大 4000、重叠 0**，两级预处理都关闭 → **一行 = 一段** |
| 确定知识库ID (`node_code_decide_id`) | 正文入库前**规整**：剥掉 markdown 代码围栏，用 `json.JSONDecoder.raw_decode` 逐个抽出 JSON 对象、重压成"一行一个 JSON 语句"；抽不到再退回按行保留。并新增输出 `segments_expected`（预计段数） |

> 为什么不直接调 Dify 的「分段对象数组」接口 `POST /v1/datasets/{ds}/documents/{doc}/segments`？
> 实测（见 `verify_segments*.py`）该接口**要求文档已索引完成**：空文本建文档会 `error`
> （`Custom segment length should be between 50 and 4000.`），正常文档建完立刻调用会
> `404 Document is not completed`（约 2 秒后才好）。走这条路必须"建占位文档 → 等完成 →
> 删占位段 → 再写数组"，多 4~5 个节点且含异步等待；而**自定义分段按 `\n` 切**的结果完全等价。

**实测结果（应用级端到端，`test_qa40.py`）**

上传 `中暑急救要点.txt`（对话附件）→ 生成 8 条问答对 → 知识库 `seg=8`，**每段正好一条 JSON 语句** ✅

```
1 :: {"question": "先兆中暑有哪些症状？", "response_chosen": "高温环境下出现头晕、口渴…"}
2 :: {"question": "出现先兆中暑应该如何处理？", "response_chosen": "应迅速离开高温环境…"}
...
8 :: {"question": "中暑急救有哪些禁忌事项？", "response_chosen": "…不要使用酒精大面积擦拭降温"}
分段数 = 8  每段都是完整 JSON 对象: ✅
```

**顺手修掉的一个既有 bug（重要）**

`是否有对话附件`（`node_if_hasfile`）的条件写成了 `has_file is ""`（空字符串），而 `has_file`
是**布尔**变量。Dify 归一化期望值时会走 `_convert_to_bool("")` → `json.loads("")` →
抛 `Expecting value: line 1 column 1 (char 0)`，**该节点必失败**。也就是说
**"对话附件"这条路径是走不通的**（只有把文件放进开始节点变量 `knowledge_file` 才能入库）。

已改为 `has_file is false`（语义就是原来那句"一个文件都没有"）。端到端实测通过。
> 同样的写法在 **问答改** 和 **问答3.0** 里也存在（`node_if_hasfile` 值都是 `''`），
> 需要的话按同样方式改一下即可。

**"每段约 500 字、一段里好几条 JSON"怎么判断是哪种原因**

看 `dataset_process_rules` 里那份规则就知道实际走的是哪套：

```sql
select mode, rules from dataset_process_rules
where dataset_id=(select dataset_id from documents where id='<文档id>')
order by created_at desc limit 1;
```

- `automatic` + `max_tokens 500` / `chunk_overlap 50` → **改动被覆盖了**（见上面的 ⚠️），
  跑 `python wf40_patch.py verify` 会报不通过，重新 `apply --publish` 并刷新页面即可。
- `custom` + `separator "\n"` → 规则已生效，若还粘在一起就看正文里是否真有换行。


