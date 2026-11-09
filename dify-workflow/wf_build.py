# -*- coding: utf-8 -*-
"""
按"步骤"构建 Dify 应用「问答改」的工作流图（graph JSON）。

设计原则：
- 纯数据生成，不连数据库；产物写到 steps/stepN.json，便于逐步骤审阅 / 回退。
- 每一步都是一个"完整且可运行"的图，越往后功能越全：
    step0 原图
    step1 开始节点加文件变量 + 开启文件上传
    step2 开始后插入【问题分类器】（医学知识文件 / 医学问题）
    step3 文件分支：文档提取器 -> 大模型生成问答对
    step4 文件分支：按文件名新建知识库 -> 写入问答对 txt
    step5 问题分支：大模型生成五个相似问 -> 代码节点解析成数组
    step6 问题分支：迭代循环（知识检索 -> 大模型逐问作答）
    step7 问题分支：大模型去重汇总 -> 直接回复
"""

import copy
import json
import os

# ---------------------------------------------------------------- 常量

APP_ID = "ce52509d-df51-4bfb-961d-5f6c9737dab4"
APP_NAME = "问答改"

# 原始节点 id（尽量复用，减少改动）
START = "1788759963315"
ORIG_KR = "1788759983275"          # 原始「知识检索」
ORIG_LLM = "llm"                   # 原始 LLM
ORIG_ANSWER = "answer"             # 原始「直接回复」

# 新增节点 id
CLASSIFIER = "node_classifier"
DOC_EXT = "node_doc_extractor"
LLM_QA = "node_llm_gen_qa"
CODE_FN = "node_code_filename"
HTTP_MK_KB = "node_http_create_kb"
CODE_DS = "node_code_dataset"
HTTP_ADD_DOC = "node_http_add_doc"
ANSWER_FILE = "node_answer_file"
LLM_SIM = "node_llm_similar"
CODE_PARSE = "node_code_parse_q"
ITER = "node_iteration"
ITER_START = "node_iteration_start"
LLM_FINAL = "node_llm_final"

# ---- step8（修复版）新增 ----
CODE_FILESRC = "node_code_filesrc"        # 早期：判断文件来源 + 文件名
IF_FILESRC = "node_if_filesrc"            # 文件来源分支
IF_HASFILE = "node_if_hasfile"            # 是否有对话附件
EXT_SYS = "node_ext_sys"                  # 文档提取器（sys.files）
EXT_VAR = "node_ext_var"                  # 文档提取器（knowledge_file）
LLM_QA_SYS = "node_llm_qa_sys"            # 生成问答对（对话附件）
LLM_QA_VAR = "node_llm_qa_var"            # 生成问答对（文件变量）
LLM_QA = "node_llm_gen_qa"        # 生成问答对（正文校验通过后）
CODE_CHECK_TEXT = "node_code_check_text"  # 校验正文是否有效
IF_TEXT_OK = "node_if_text_ok"     # 正文是否有效
ANSWER_BADFILE = "node_answer_badfile"    # 未提取到正文时的提示
AGG_TEXT = "node_agg_text"                # 正文文本聚合
ANSWER_HINT = "node_answer_hint"          # 无文件时的提示回复
HTTP_LIST_KB = "node_http_list_kb"        # 查知识库列表
CODE_FIND_KB = "node_code_find_kb"        # 找同名知识库
IF_KB = "node_if_kb"                      # 是否存在
CODE_DECIDE_ID = "node_code_decide_id"    # 决定用哪个知识库 + 转义正文

LLM_PROVIDER = "langgenius/siliconflow/siliconflow"
LLM_MODEL = "deepseek-ai/DeepSeek-V3.2"
CLS_PROVIDER = "langgenius/siliconflow/siliconflow"
CLS_MODEL = "deepseek-ai/DeepSeek-V3.2"

DATASET_API_KEY = "dataset-REPLACE_WITH_YOUR_KEY"
API_BASE = "http://api:5001/v1"
EMBEDDING_MODEL = "Pro/BAAI/bge-m3"
EMBEDDING_PROVIDER = "langgenius/siliconflow/siliconflow"

# 原始「知识检索」节点里配置的 9 个知识库
DATASET_IDS = [
    "4657f032-fd37-4763-9208-1f8d907d244d",
    "1850109b-6d81-4f7f-9138-28be896ba0b9",
    "079a4b2b-6cac-43fa-b415-3363be7cef2b",
    "55f0131b-7c4c-43ab-a9f9-d7884fe4dea0",
    "5e2f6f97-17f9-400b-880a-a64774710f37",
    "667de273-df3e-4f96-bf8b-ad68e5b32796",
    "1098ee63-0ce6-41ea-b55f-cf7596573140",
    "15d8084e-5807-40ae-b942-adbcb7721cf8",
    "b98955c3-0637-433c-b0d1-8a80749f603b",
]

DOC_EXTENSIONS = [
    ".TXT", ".MD", ".MARKDOWN", ".PDF", ".HTML", ".HTM", ".XLSX", ".XLS",
    ".DOCX", ".DOC", ".CSV", ".EML", ".MSG", ".PPTX", ".PPT", ".XML",
    ".EPUB", ".JSON",
]

FILE_VAR = {
    "variable": "knowledge_file",
    "label": "医学知识文件",
    "description": "上传医学知识文件（txt/md/pdf/docx 等），系统会自动生成问答对并入库",
    # 必须是 file-list：单个 file 变量在代码节点里会被转成 File 对象，
    # 无法 JSON 序列化（报 "Type is not JSON serializable: File"）。
    "type": "file-list",
    "required": False,
    "hide": False,
    "default": None,
    "max_length": None,
    "options": [],
    "allowed_file_types": ["document"],
    "allowed_file_extensions": DOC_EXTENSIONS,
    "allowed_file_upload_methods": ["local_file", "remote_url"],
}

ORIGINAL_GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "backup", "wf_ce52509d_original_graph.json",
)

STEP_TITLES = {
    0: "原始流程（未修改）",
    1: "开始节点：增加文件变量 + 开启文件上传",
    2: "开始节点后：插入问题分类器（医学知识文件 / 医学问题）",
    3: "文件分支：文档提取器 -> 大模型生成问答对",
    4: "文件分支：按文件名新建知识库并写入 txt",
    5: "问题分支：大模型生成五个相似问并解析成数组",
    6: "问题分支：迭代循环检索（知识检索 + 逐问作答）",
    7: "问题分支：大模型去重汇总 -> 直接回复（完整版）",
    8: "修复：兼容两种上传位置 + 同名知识库自动复用（不再 409）",
}

# ---------------------------------------------------------------- 小工具


def _n(nid, x, y, data, *, w=242, h=72, parent=None, z=0, node_type="custom"):
    """构造一个画布节点。"""
    n = {
        "data": data,
        "height": h,
        "id": nid,
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": node_type,
        "width": w,
        "zIndex": z,
    }
    if parent:
        n["parentId"] = parent
    return n


def _e(src, tgt, src_handle="source", tgt_handle="target",
       src_type=None, tgt_type=None, iteration_id=None):
    """构造一条边。"""
    if not src_type:
        src_type = _TYPE_OF.get(src, "")
    if not tgt_type:
        tgt_type = _TYPE_OF.get(tgt, "")
    return {
        "data": {
            "isInIteration": bool(iteration_id),
            "isInLoop": False,
            "sourceType": src_type,
            "targetType": tgt_type,
            "iteration_id": iteration_id,
            "loop_id": None,
        },
        "id": f"{src}-{src_handle}-{tgt}-{tgt_handle}",
        "source": src,
        "sourceHandle": src_handle,
        "target": tgt,
        "targetHandle": tgt_handle,
        "type": "custom",
        "zIndex": 1002 if iteration_id else 0,
    }


_TYPE_OF = {
    START: "start",
    ORIG_KR: "knowledge-retrieval",
    ORIG_LLM: "llm",
    ORIG_ANSWER: "answer",
    CLASSIFIER: "question-classifier",
    DOC_EXT: "document-extractor",
    LLM_QA: "llm",
    CODE_FN: "code",
    HTTP_MK_KB: "http-request",
    CODE_DS: "code",
    HTTP_ADD_DOC: "http-request",
    ANSWER_FILE: "answer",
    LLM_SIM: "llm",
    CODE_PARSE: "code",
    ITER: "iteration",
    ITER_START: "iteration-start",
    LLM_FINAL: "llm",
    CODE_FILESRC: "code",
    IF_FILESRC: "if-else",
    IF_HASFILE: "if-else",
    EXT_SYS: "document-extractor",
    EXT_VAR: "document-extractor",
    LLM_QA_SYS: "llm",
    LLM_QA_VAR: "llm",
    LLM_QA: "llm",
    CODE_CHECK_TEXT: "code",
    IF_TEXT_OK: "if-else",
    ANSWER_BADFILE: "answer",
    AGG_TEXT: "variable-aggregator",
    ANSWER_HINT: "answer",
    HTTP_LIST_KB: "http-request",
    CODE_FIND_KB: "code",
    IF_KB: "if-else",
    CODE_DECIDE_ID: "code",
}


def _llm_data(title, system_prompt, user_prompt, *, context_selector=None,
              desc=""):
    return {
        "type": "llm",
        "title": title,
        "desc": desc,
        "model": {
            "provider": LLM_PROVIDER,
            "name": LLM_MODEL,
            "mode": "chat",
            "completion_params": {"temperature": 0.3},
        },
        "prompt_template": [
            {"id": "sys-" + title, "role": "system", "text": system_prompt},
            {"id": "usr-" + title, "role": "user", "text": user_prompt},
        ],
        "prompt_config": {"jinja2_variables": []},
        "memory": None,
        "context": {
            "enabled": bool(context_selector),
            "variable_selector": context_selector,
        },
        "vision": {"enabled": False},
        "selected": False,
    }


QA_SYSTEM_PROMPT = (
    "你是医学知识整理助手。请把给定的医学文档内容拆解成「问答对」。\n"
    "要求：\n"
    "1. 每行输出一个 JSON 对象，严格使用格式："
    "{\"question\": \"问题\", \"response_chosen\": \"答案\"}\n"
    "2. 只输出 JSON，每行一个；不要输出解释、标题、Markdown 代码块或多余文字。\n"
    "3. 问题要具体、自然、可独立成问；答案必须完全依据文档内容，不得编造。\n"
    "4. 尽可能覆盖文档中的全部知识点。\n"
    "示例：{\"question\": \"什么是尿路刺激征？\", "
    "\"response_chosen\": \"是指膀胱颈和膀胱三角区受炎症或机械刺激而引起的"
    "尿频、尿急、尿痛，伴有排尿不尽感及下腹坠痛\"}"
)


def _llm_qa_data(title, extractor_id):
    """「生成问答对」大模型节点（内容取自指定文档提取器的 text 输出）。"""
    return _llm_data(
        title,
        QA_SYSTEM_PROMPT,
        "文档内容：\n{{#%s.text#}}" % extractor_id,
    )


def _llm_qa_from_agg(title, agg_id):
    """「生成问答对」：内容取自 variable-aggregator 的 output。"""
    return _llm_data(
        title,
        QA_SYSTEM_PROMPT,
        "文档内容：\n{{#%s.output#}}" % agg_id,
    )


# ---------------------------------------------------------------- 图构建


def build_graph(step: int) -> dict:
    """返回第 step 步的完整 graph。"""
    if step <= 1:
        g = _load_original()
        if step == 1:
            for n in g["nodes"]:
                if n["id"] == START:
                    n["data"]["variables"] = [dict(FILE_VAR)]
        return g

    nodes = []
    edges = []

    # ---------------- 开始节点
    start_vars = [dict(FILE_VAR)]
    nodes.append(_n(START, 80, 320, {
        "desc": "", "type": "start", "title": "开始",
        "variables": start_vars, "selected": False,
    }, h=72))

    # ---------------- 原「知识检索」：step<6 在主干，step>=6 搬进迭代
    kr = copy.deepcopy(ORIG_KR_DATA)
    if step >= 6:
        kr["query_variable_selector"] = [ITER, "item"]
    else:
        kr["query_variable_selector"] = [START, "sys.query"]
    kr["selected"] = False

    # ---------------- 原 LLM：step<6 使用 sys.query，step>=6 使用迭代项
    if step >= 6:
        llm_main = _llm_data(
            "逐问作答",
            "你是医学知识助手。请**只依据**下面《知识库检索结果》回答用户问题；"
            "若检索结果为空或与问题无关，直接回答「知识库中未找到相关内容」。"
            "不要编造。\n\n《知识库检索结果》：\n{{#context#}}",
            "{{#%s.item#}}" % ITER,
            context_selector=[ORIG_KR, "result"],
        )
    else:
        llm_main = {
            "variables": [],
            "prompt_template": [
                {"id": "d64e5603-0eff-4f10-bff8-fae65c054ada", "role": "system",
                 "text": "针对问题{{#sys.query#}}和知识库返回的内容{{#context#}}，进行润色，只返回问题相关的内容，只要总结结果"},
                {"id": "80f4d397-5a1a-4fb4-8949-ba1a0e2b7577", "role": "user", "text": ""},
            ],
            "desc": "",
            "context": {"enabled": True, "variable_selector": [ORIG_KR, "result"]},
            "title": "LLM",
            "type": "llm",
            "memory": {
                "query_prompt_template": "{{#sys.query#}}",
                "role_prefix": {"assistant": "", "user": ""},
                "window": {"size": 10, "enabled": False},
            },
            "vision": {"enabled": False},
            "model": {
                "name": LLM_MODEL, "provider": LLM_PROVIDER,
                "completion_params": {}, "mode": "chat",
            },
            "selected": False,
        }

    if step < 6:
        # 主干：知识检索 -> LLM -> 直接回复
        nodes.append(_n(ORIG_KR, 700, 320, kr, h=51, w=241))
        nodes.append(_n(ORIG_LLM, 1000, 320, llm_main, h=72, w=242))
    else:
        # 迭代容器 + 内部节点
        iter_x, iter_y = 1300, 260
        nodes.append(_n(ITER, iter_x, iter_y, {
            "desc": "",
            "type": "iteration",
            "title": "循环检索知识库回答",
            "start_node_id": ITER_START,
            "iterator_selector": [CODE_PARSE, "questions"],
            "iterator_input_type": "array[string]",
            "output_selector": [ORIG_LLM, "text"],
            "output_type": "array[string]",
            "is_parallel": False,
            "parallel_nums": 10,
            "error_handle_mode": "terminated",
            "flatten_output": True,
            "height": 250,
            "width": 860,
            "selected": False,
        }, w=860, h=250, z=1))
        nodes.append(_n(ITER_START, 24, 70, {
            "desc": "", "isInIteration": True, "selected": False,
            "title": "", "type": "iteration-start",
        }, w=44, h=48, parent=ITER, z=1002, node_type="custom-iteration-start"))
        kr["isInIteration"] = True
        kr["isInLoop"] = False
        kr["iteration_id"] = ITER
        llm_main["isInIteration"] = True
        llm_main["isInLoop"] = False
        llm_main["iteration_id"] = ITER
        nodes.append(_n(ORIG_KR, 140, 70, kr, h=51, w=241,
                        parent=ITER, z=1002))
        nodes.append(_n(ORIG_LLM, 470, 70, llm_main, h=72, w=242,
                        parent=ITER, z=1002))

    # ---------------- 问题分类器（step>=2）
    if step >= 2:
        nodes.append(_n(CLASSIFIER, 380, 320, {
            "type": "question-classifier",
            "title": "问题分类器",
            "desc": "判断用户这次输入的是「医学知识文件」还是「医学问题」",
            "query_variable_selector": [START, "sys.query"],
            "classes": [
                {"id": "1", "name": "医学知识文件", "label": "CLASS 1"},
                {"id": "2", "name": "医学问题", "label": "CLASS 2"},
            ],
            "instruction": (
                "如果用户上传了文件、或消息里说明要「上传/录入/整理一份医学知识资料」，"
                "归类为「医学知识文件」；其余提问一律归类为「医学问题」。"
            ),
            "model": {
                "provider": CLS_PROVIDER,
                "name": CLS_MODEL,
                "mode": "chat",
                "completion_params": {"temperature": 0.01},
            },
            "vision": {"enabled": False},
            "selected": False,
        }, h=112))
        edges.append(_e(START, CLASSIFIER))

    # ---------------- 文件分支
    if step >= 8:
        _build_file_branch_fixed(nodes, edges)
    elif step >= 2:
        # 分支出口先落到一个「直接回复」，保证任意步骤下图都是完整可跑的
        if step < 3:
            nodes.append(_n(ANSWER_FILE, 1300, 120, {
                "answer": "已进入「医学知识文件」分支（该分支还在搭建中）。",
                "desc": "", "type": "answer", "title": "文件分支-回复",
                "variables": [], "selected": False,
            }, h=101))
            edges.append(_e(CLASSIFIER, ANSWER_FILE, src_handle="1"))
        else:
            nodes.append(_n(DOC_EXT, 700, 120, {
                "type": "document-extractor",
                "title": "文档提取器",
                "desc": "读取上传文件里的纯文本",
                "variable_selector": ["sys", "files"],
                "selected": False,
            }, h=51))
            nodes.append(_n(LLM_QA, 1000, 60, _llm_data(
                "生成问答对",
                "你是医学知识整理助手。请把给定的医学文档内容拆解成「问答对」。\n"
                "要求：\n"
                "1. 每行输出一个 JSON 对象，严格使用格式："
                "{\"question\": \"问题\", \"response_chosen\": \"答案\"}\n"
                "2. 只输出 JSON，每行一个；不要输出解释、标题、Markdown 代码块或多余文字。\n"
                "3. 问题要具体、自然、可独立成问；答案必须完全依据文档内容，不得编造。\n"
                "4. 尽可能覆盖文档中的全部知识点。\n"
                "示例：{\"question\": \"什么是尿路刺激征？\", "
                "\"response_chosen\": \"是指膀胱颈和膀胱三角区受炎症或机械刺激而引起的"
                "尿频、尿急、尿痛，伴有排尿不尽感及下腹坠痛\"}",
                "文档内容：\n{{#%s.text#}}" % DOC_EXT,
            ), h=72))
            edges.append(_e(CLASSIFIER, DOC_EXT, src_handle="1"))
            edges.append(_e(DOC_EXT, LLM_QA))

            target = ANSWER_FILE
            if step >= 4:
                nodes.append(_n(CODE_FN, 1280, 120, _code(
                    "取文件名并转义",
                    CODE_PREP_SOURCE,
                    [{"variable": "files", "value_selector": ["sys", "files"],
                      "value_type": "array[file]"},
                     {"variable": "text", "value_selector": [LLM_QA, "text"],
                      "value_type": "string"}],
                    {"filename": {"type": "string", "children": None},
                     "filename_esc": {"type": "string", "children": None},
                     "text_esc": {"type": "string", "children": None}},
                ), h=72))
                nodes.append(_n(HTTP_MK_KB, 1580, 120, _http(
                    "新建知识库", "POST", API_BASE + "/datasets", JSON_CREATE_KB,
                ), h=72))
                nodes.append(_n(CODE_DS, 1880, 120, _code(
                    "取知识库ID",
                    "import json\n"
                    "def main(body: str) -> dict:\n"
                    "    try:\n"
                    "        data = json.loads(body)\n"
                    "    except Exception:\n"
                    "        return {\"dataset_id\": \"\", \"ok\": False, \"msg\": (body or \"\")[:300]}\n"
                    "    did = data.get(\"id\", \"\") if isinstance(data, dict) else \"\"\n"
                    "    return {\"dataset_id\": str(did), \"ok\": bool(did),\n"
                    "            \"msg\": (body or \"\")[:300]}\n",
                    [{"variable": "body", "value_selector": [HTTP_MK_KB, "body"],
                      "value_type": "string"}],
                    {"dataset_id": {"type": "string", "children": None},
                     "ok": {"type": "boolean", "children": None},
                     "msg": {"type": "string", "children": None}},
                ), h=72))
                nodes.append(_n(HTTP_ADD_DOC, 2180, 120, _http(
                    "写入问答对txt",
                    "POST",
                    API_BASE + "/datasets/{{#%s.dataset_id#}}/document/create-by-text" % CODE_DS,
                    JSON_ADD_DOC,
                ), h=72))
                edges.append(_e(LLM_QA, CODE_FN))
                edges.append(_e(CODE_FN, HTTP_MK_KB))
                edges.append(_e(HTTP_MK_KB, CODE_DS))
                edges.append(_e(CODE_DS, HTTP_ADD_DOC))
                edges.append(_e(HTTP_ADD_DOC, ANSWER_FILE))

            if step < 4:
                edges.append(_e(LLM_QA, ANSWER_FILE))

            nodes.append(_n(ANSWER_FILE, 2500 if step >= 4 else 1600, 120, {
                "answer": _file_answer(step),
                "desc": "", "type": "answer", "title": "文件分支-回复",
                "variables": [], "selected": False,
            }, h=101))

    # ---------------- 问题分支
    if step >= 2:
        # step2/3/4：分类器 class2 直接进原链路（知识检索 -> LLM -> 直接回复）
        if step < 5:
            edges.append(_e(CLASSIFIER, ORIG_KR, src_handle="2"))
        else:
            nodes.append(_n(LLM_SIM, 700, 460, _llm_data(
                "生成五个相似问",
                "你是医学问答助手。请把用户的问题改写成 5 个语义相同、但说法不同的提问，"
                "用于扩大知识库检索的召回范围。\n"
                "要求：\n"
                "1. 只输出一个 JSON 字符串数组，不要输出解释或 Markdown 代码块。\n"
                "2. 数组共 5 个字符串元素。\n"
                "3. 覆盖不同说法：口语化、书面化、近义症状描述、专业术语等。\n"
                "示例：[\"什么是尿路刺激征？\", \"尿路刺激征是什么意思？\", "
                "\"尿频尿急尿痛合称什么？\", \"膀胱刺激征的临床表现有哪些？\", "
                "\"小便次数多、憋不住、排尿疼是什么病？\"]",
                "{{#sys.query#}}",
            ), h=72))
            nodes.append(_n(CODE_PARSE, 1000, 460, _code(
                "解析相似问",
                "import json, re\n"
                "def main(text: str) -> dict:\n"
                "    s = (text or \"\").strip()\n"
                "    m = re.search(r\"\\[.*\\]\", s, re.S)\n"
                "    if m:\n"
                "        s = m.group(0)\n"
                "    try:\n"
                "        arr = json.loads(s)\n"
                "    except Exception:\n"
                "        arr = []\n"
                "    if not isinstance(arr, list):\n"
                "        arr = []\n"
                "    qs = []\n"
                "    for x in arr:\n"
                "        if isinstance(x, str) and x.strip():\n"
                "            qs.append(x.strip())\n"
                "        elif isinstance(x, dict):\n"
                "            q = x.get(\"question\") or x.get(\"q\")\n"
                "            if q:\n"
                "                qs.append(str(q).strip())\n"
                "    seen, out = set(), []\n"
                "    for q in qs:\n"
                "        if q not in seen:\n"
                "            seen.add(q)\n"
                "            out.append(q)\n"
                "    if not out:\n"
                "        out = [s] if s else [\"医学问题\"]\n"
                "    return {\"questions\": out}\n",
                [{"variable": "text", "value_selector": [LLM_SIM, "text"],
                  "value_type": "string"}],
                {"questions": {"type": "array[string]", "children": None}},
            ), h=72))
            edges.append(_e(CLASSIFIER, LLM_SIM, src_handle="2"))
            edges.append(_e(LLM_SIM, CODE_PARSE))

            if step < 6:
                edges.append(_e(CODE_PARSE, ORIG_KR))
            else:
                edges.append(_e(CODE_PARSE, ITER))
                edges.append(_e(ITER_START, ORIG_KR, iteration_id=ITER))
                edges.append(_e(ORIG_KR, ORIG_LLM, iteration_id=ITER))

                if step >= 7:
                    nodes.append(_n(LLM_FINAL, 2200, 460, _llm_data(
                        "去重汇总作答",
                        "你是医学知识助手。下面是对同一个用户问题"
                        "（{{#sys.query#}}）用多种相似问法分别检索知识库后得到的若干条回答。"
                        "请：\n"
                        "1. 去掉重复、冗余、与问题无关的内容；\n"
                        "2. 合并成一个准确、完整、条理清晰的最终回答；\n"
                        "3. 只依据这些内容作答，不得编造；若全部为空，"
                        "说明「知识库中未找到相关内容」。",
                        "{{#%s.output#}}" % ITER,
                    ), h=72))
                    edges.append(_e(ITER, LLM_FINAL))
                    edges.append(_e(LLM_FINAL, ORIG_ANSWER))
                else:
                    edges.append(_e(ITER, ORIG_ANSWER))
        # 原始「直接回复」
        nodes.append(_n(ORIG_ANSWER, 2500, 460, {
            "answer": ("{{#%s.text#}}" % LLM_FINAL) if step >= 7 else "{{#llm.text#}}",
            "desc": "", "type": "answer", "title": "直接回复",
            "variables": [], "selected": False,
        }, h=101))

        # 主干原链路：知识检索 -> LLM -> 直接回复（step<6 时挂在分类器分支 2 下）
        if step < 6:
            edges.append(_e(ORIG_KR, ORIG_LLM))
            edges.append(_e(ORIG_LLM, ORIG_ANSWER))

    viewport = {"x": -60, "y": 40, "zoom": 0.72}
    return {"nodes": nodes, "edges": edges, "viewport": viewport}


def _build_file_branch_fixed(nodes, edges):
    """
    step8 的文件分支：
      - 兼容两种上传位置：对话框附件（sys.files）与开始节点的「医学知识文件」变量
      - 没传文件时给出明确提示
      - 文档提取失败 / 正文为空时，**不再**让大模型编出"文档内容为空"的假问答对
      - 同名知识库自动复用，不再 409

    说明：Dify 的「文档提取器」只能读静态的变量选择器，代码节点又不能输出 file 类型，
    所以用「判断来源 -> 两条平行子路径（各自提取）-> 汇合 -> 校验 -> 生成问答对」的写法。
    一次请求只会走其中一条子路径，另一条不会执行。
    """
    y = 40

    nodes.append(_n(CODE_FILESRC, 700, y, _code(
        "取文件名/判断来源",
        CODE_FILESRC_CODE,
        [{"variable": "files", "value_selector": ["sys", "files"],
          "value_type": "array[file]"},
         {"variable": "file_var", "value_selector": [START, "knowledge_file"],
          "value_type": "array[file]"}],
        {"filename": {"type": "string", "children": None},
         "filename_esc": {"type": "string", "children": None},
         "source": {"type": "string", "children": None},
         "has_file": {"type": "boolean", "children": None}},
        desc="文件来自对话附件还是开始节点的文件变量",
    ), h=72))

    nodes.append(_n(IF_FILESRC, 1000, y, _ifelse(
        "判断文件来源",
        [("1", [([CODE_FILESRC, "source"], "is", "var")])],
        desc="命中 1 = 用「医学知识文件」变量；否则（false）= 用对话附件或没有文件再判断",
    ), h=112))

    # ---- 没有文件变量时，看有没有对话附件
    nodes.append(_n(IF_HASFILE, 1180, y - 180, _ifelse(
        "是否有对话附件",
        [("1", [([CODE_FILESRC, "has_file"], "is", False)])],
        desc="命中 1 = 一个文件都没有，直接给提示",
    ), h=112))

    # ---- 子路径 A：对话附件（sys.files）
    nodes.append(_n(EXT_SYS, 1420, y - 280, _extractor(
        "文档提取器(对话附件)", ["sys", "files"],
        desc="读取对话框附件里的纯文本"), h=51))

    # ---- 子路径 B：开始节点「医学知识文件」
    nodes.append(_n(EXT_VAR, 1420, y + 150, _extractor(
        "文档提取器(文件变量)", [START, "knowledge_file"],
        desc="读取开始节点「医学知识文件」里的纯文本"), h=51))

    # ---- 无文件提示
    nodes.append(_n(ANSWER_HINT, 1420, y + 400, {
        "answer": ("没有收到文件内容。请在**对话框的附件按钮**上传文件，"
                   "或在开始节点的「医学知识文件」里选择文件后重试。"),
        "desc": "", "type": "answer", "title": "提示-未上传文件",
        "variables": [], "selected": False,
    }, h=101))

    # ---- 汇合：两条子路径只会跑一条，取有内容的那个
    nodes.append(_n(AGG_TEXT, 1700, y, _aggregator(
        "正文汇总", [[EXT_SYS, "text"], [EXT_VAR, "text"]],
        desc="两条子路径只会跑一条", output_type="array[string]"), h=72))

    # ---- 校验正文：提取失败 / 内容为空时，短路到提示，不喂给大模型
    nodes.append(_n(CODE_CHECK_TEXT, 1960, y, _code(
        "校验正文",
        CODE_CHECK_TEXT_CODE,
        [{"variable": "doc_text", "value_selector": [EXT_SYS, "text"],
          "value_type": "array[string]"},
         {"variable": "var_text", "value_selector": [EXT_VAR, "text"],
          "value_type": "array[string]"},
         {"variable": "merged", "value_selector": [AGG_TEXT, "output"],
          "value_type": "string"}],
        {"ok": {"type": "boolean", "children": None},
         "reason": {"type": "string", "children": None},
         "doc_chars": {"type": "number", "children": None}},
        desc="判断文档是否真的提取出了正文内容",
    ), h=72))

    nodes.append(_n(IF_TEXT_OK, 2220, y, _ifelse(
        "正文是否有效",
        [("bad", [([CODE_CHECK_TEXT, "ok"], "is", False)])],
        desc="命中 bad = 没提取到正文，给提示；false = 正常生成问答对并入库",
    ), h=112))

    nodes.append(_n(ANSWER_BADFILE, 2220, y + 330, {
        "answer": ("⚠️ 这份文件没有提取到可用于生成问答对的正文，已跳过入库。\n\n"
                   "可能原因：\n"
                   "- 文件是扫描件 / 图片型 PDF，没有文字层；\n"
                   "- DOCX 内部结构损坏（例如内嵌图片 CRC 校验失败）；\n"
                   "- 文档本身就是空的。\n\n"
                   "已提取到的正文字符数：{{#%s.doc_chars#}}\n"
                   "（提示：{{#%s.reason#}}）"
                   % (CODE_CHECK_TEXT, CODE_CHECK_TEXT)),
        "desc": "", "type": "answer", "title": "提示-未能提取正文",
        "variables": [], "selected": False,
    }, h=101))

    # ---- 生成问答对（正文确认有效之后）
    nodes.append(_n(LLM_QA, 2480, y, _llm_qa_from_agg("生成问答对", AGG_TEXT), h=72))

    nodes.append(_n(HTTP_LIST_KB, 2760, y, _http(
        "查知识库列表", "GET", API_BASE + "/datasets?limit=100",
        desc="列出全部知识库，用于判断同名库是否已存在"), h=72))

    nodes.append(_n(CODE_FIND_KB, 3020, y, _code(
        "查找同名知识库",
        CODE_FIND_KB_CODE,
        [{"variable": "body", "value_selector": [HTTP_LIST_KB, "body"],
          "value_type": "string"},
         {"variable": "filename", "value_selector": [CODE_FILESRC, "filename"],
          "value_type": "string"}],
        {"dataset_id": {"type": "string", "children": None},
         "exists": {"type": "boolean", "children": None},
         "matched": {"type": "string", "children": None}},
        desc="按文件名精确匹配已存在的知识库",
    ), h=72))

    nodes.append(_n(IF_KB, 3280, y, _ifelse(
        "知识库是否已存在",
        [("create", [([CODE_FIND_KB, "exists"], "is", False)])],
        desc="不存在才去新建；已存在就直接复用，避免 409"),
        h=112))

    nodes.append(_n(HTTP_MK_KB, 3560, y - 150, _http(
        "新建知识库", "POST", API_BASE + "/datasets", JSON_CREATE_KB_FIXED,
        desc="按文件名新建知识库",
        error_strategy="default-value",
        default_value=[{"key": "body", "type": "string", "value": "{}"},
                       {"key": "status_code", "type": "number", "value": 0}],
    ), h=72))

    nodes.append(_n(CODE_DECIDE_ID, 3860, y, _code(
        "确定知识库ID",
        CODE_DECIDE_ID_CODE,
        [{"variable": "find_id", "value_selector": [CODE_FIND_KB, "dataset_id"],
          "value_type": "string"},
         {"variable": "create_body", "value_selector": [HTTP_MK_KB, "body"],
          "value_type": "string"},
         {"variable": "text", "value_selector": [LLM_QA, "text"],
          "value_type": "string"}],
        {"dataset_id": {"type": "string", "children": None},
         "how": {"type": "string", "children": None},
         "text_esc": {"type": "string", "children": None}},
        desc="已存在就用已有的，否则用刚新建的；顺便转义正文",
    ), h=72))

    nodes.append(_n(HTTP_ADD_DOC, 4140, y, _http(
        "写入问答对txt",
        "POST",
        API_BASE + "/datasets/{{#%s.dataset_id#}}/document/create-by-text" % CODE_DECIDE_ID,
        JSON_ADD_DOC_FIXED,
    ), h=72))

    nodes.append(_n(ANSWER_FILE, 4440, y, {
        "answer": ("✅ 已处理文件《{{#%s.filename#}}》\n\n"
                   "- 知识库：**{{#%s.filename#}}**（{{#%s.how#}}）\n"
                   "- 知识库 ID：`{{#%s.dataset_id#}}`\n"
                   "- 生成的问答对：\n```\n{{#%s.text#}}\n```"
                   % (CODE_FILESRC, CODE_FILESRC, CODE_DECIDE_ID,
                      CODE_DECIDE_ID, LLM_QA)),
        "desc": "", "type": "answer", "title": "文件分支-回复",
        "variables": [], "selected": False,
    }, h=101))

    edges.append(_e(CLASSIFIER, CODE_FILESRC, src_handle="1"))
    edges.append(_e(CODE_FILESRC, IF_FILESRC))
    # IF_FILESRC: 命中 1 -> 用「医学知识文件」变量；false -> 再看有没有对话附件
    edges.append(_e(IF_FILESRC, EXT_VAR, src_handle="1"))
    edges.append(_e(IF_FILESRC, IF_HASFILE, src_handle="false"))
    # IF_HASFILE: 命中 1 -> 没文件，给提示；false -> 走对话附件
    edges.append(_e(IF_HASFILE, ANSWER_HINT, src_handle="1"))
    edges.append(_e(IF_HASFILE, EXT_SYS, src_handle="false"))
    # 两条子路径各自提取 -> 汇合
    edges.append(_e(EXT_SYS, AGG_TEXT))
    edges.append(_e(EXT_VAR, AGG_TEXT))
    # 校验正文
    edges.append(_e(AGG_TEXT, CODE_CHECK_TEXT))
    edges.append(_e(CODE_CHECK_TEXT, IF_TEXT_OK))
    edges.append(_e(IF_TEXT_OK, ANSWER_BADFILE, src_handle="bad"))
    edges.append(_e(IF_TEXT_OK, LLM_QA, src_handle="false"))
    # 生成问答对 -> 入库
    edges.append(_e(LLM_QA, HTTP_LIST_KB))
    edges.append(_e(HTTP_LIST_KB, CODE_FIND_KB))
    edges.append(_e(CODE_FIND_KB, IF_KB))
    edges.append(_e(IF_KB, HTTP_MK_KB, src_handle="create"))
    edges.append(_e(IF_KB, CODE_DECIDE_ID, src_handle="false"))
    edges.append(_e(HTTP_MK_KB, CODE_DECIDE_ID))
    edges.append(_e(CODE_DECIDE_ID, HTTP_ADD_DOC))
    edges.append(_e(HTTP_ADD_DOC, ANSWER_FILE))


def _file_answer(step):
    if step < 3:
        return "已进入「医学知识文件」分支（该分支还在搭建中）。"
    if step < 4:
        return "已解析上传文件并生成问答对：\n\n{{#%s.text#}}" % LLM_QA
    return (
        "✅ 已处理文件《{{#%s.filename#}}》\n\n"
        "1. 已按文件名新建知识库：**{{#%s.filename#}}**\n"
        "2. 已将生成的问答对 txt 写入该知识库\n\n"
        "写入结果（原文）：\n{{#%s.body#}}"
        % (CODE_FN, CODE_FN, HTTP_ADD_DOC)
    )


def _load_original():
    with open(ORIGINAL_GRAPH_PATH, encoding="utf-8") as f:
        return json.load(f)


def _ifelse(title, cases, desc=""):
    """构造条件分支节点。cases: [(case_id, [(selector, op, value)])]"""
    return {
        "type": "if-else",
        "title": title,
        "desc": desc,
        "logical_operator": "and",
        "cases": [
            {
                "case_id": cid,
                "logical_operator": "and",
                "conditions": [
                    {"variable_selector": sel, "comparison_operator": op,
                     "value": val}
                    for (sel, op, val) in conds
                ],
            }
            for cid, conds in cases
        ],
        "selected": False,
    }


def _code(title, code, variables, outputs, desc=""):
    return {
        "type": "code",
        "title": title,
        "desc": desc,
        "code_language": "python3",
        "code": code,
        "variables": variables,
        "outputs": outputs,
        "dependencies": [],
        "selected": False,
    }


def _extractor(title, selector, desc=""):
    """文档提取器；读不到文件时降级为空文本，而不是让整个流程失败。"""
    return {
        "type": "document-extractor",
        "title": title,
        "desc": desc,
        "variable_selector": list(selector),
        "error_strategy": "default-value",
        "default_value": [{"key": "text", "type": "string", "value": ""}],
        "retry_config": {"max_retries": 0, "retry_interval": 0, "retry_enabled": False},
        "selected": False,
    }


def _aggregator(title, selectors, desc="", output_type="string"):
    return {
        "type": "variable-aggregator",
        "title": title,
        "desc": desc,
        "output_type": output_type,
        "variables": [list(s) for s in selectors],
        "advanced_settings": {"group_enabled": False, "groups": []},
        "selected": False,
    }


def _http(title, method, url, json_template=None, desc="",
          error_strategy=None, default_value=None):
    """
    构造 HTTP 请求节点。
    注意：Dify 的 json 体要求 data 有且仅有 1 个元素，且该元素的 value 是"整段 JSON 文本"。
    """
    if json_template is not None:
        body = {"type": "json",
                "data": [{"key": "", "type": "text", "value": json_template}]}
    else:
        body = {"type": "none", "data": []}
    node = {
        "type": "http-request",
        "title": title,
        "desc": desc,
        "method": method.lower(),
        "url": url,
        "authorization": {
            "type": "api-key",
            "config": {"type": "bearer", "api_key": DATASET_API_KEY, "header": ""},
        },
        "headers": "Content-Type:application/json",
        "params": "",
        "body": body,
        "timeout": {"connect": 10, "read": 120, "write": 60},
        "ssl_verify": True,
        "selected": False,
    }
    if error_strategy:
        node["error_strategy"] = error_strategy
    if default_value:
        node["default_value"] = default_value
    return node


# JSON 体模板（代码节点先把动态内容做 JSON 转义，保证拼出来仍是合法 JSON）
# 注意：这里引用的必须是"当前步骤里真实存在"的节点 id。
JSON_CREATE_KB_FIXED = (
    '{"name": "{{#%s.filename_esc#}}", "indexing_technique": "high_quality", '
    '"permission": "only_me", "provider": "vendor", '
    '"embedding_model": "%s", "embedding_model_provider": "%s"}'
    % (CODE_FILESRC, EMBEDDING_MODEL, EMBEDDING_PROVIDER)
)

JSON_CREATE_KB = (
    '{"name": "{{#%s.filename_esc#}}", "indexing_technique": "high_quality", '
    '"permission": "only_me", "provider": "vendor", '
    '"embedding_model": "%s", "embedding_model_provider": "%s"}'
    % (CODE_FN, EMBEDDING_MODEL, EMBEDDING_PROVIDER)
)

JSON_ADD_DOC = (
    '{"name": "{{#%s.filename_esc#}}", "text": "{{#%s.text_esc#}}", '
    '"indexing_technique": "high_quality", "process_rule": {"mode": "automatic"}}'
    % (CODE_FN, CODE_FN)
)

# ---- step8（修复版）的 JSON 体 ----

JSON_ADD_DOC_FIXED = (
    '{"name": "{{#%s.filename_esc#}}", "text": "{{#%s.text_esc#}}", '
    '"indexing_technique": "high_quality", "process_rule": {"mode": "automatic"}}'
    % (CODE_FILESRC, CODE_DECIDE_ID)
)

CODE_FILESRC_CODE = (
    "import json\n"
    "\n"
    "def _name_of(f):\n"
    "    if f is None:\n"
    "        return None\n"
    "    if isinstance(f, str):\n"
    "        return f or None\n"
    "    if isinstance(f, dict):\n"
    "        return f.get(\"name\") or f.get(\"filename\") or None\n"
    "    return getattr(f, \"name\", None) or getattr(f, \"filename\", None)\n"
    "\n"
    "def _as_list(x):\n"
    "    if x is None:\n"
    "        return []\n"
    "    if isinstance(x, list):\n"
    "        return x\n"
    "    return [x]\n"
    "\n"
    "def main(files: list, file_var) -> dict:\n"
    "    sys_files = _as_list(files)\n"
    "    var_files = _as_list(file_var)\n"
    "    if sys_files:\n"
    "        src = \"sys\"\n"
    "        name = _name_of(sys_files[0])\n"
    "    elif var_files:\n"
    "        src = \"var\"\n"
    "        name = _name_of(var_files[0])\n"
    "    else:\n"
    "        src = \"none\"\n"
    "        name = None\n"
    "    name = str(name) if name else \"医学知识文件\"\n"
    "    return {\n"
    "        \"filename\": name,\n"
    "        \"filename_esc\": json.dumps(name, ensure_ascii=False)[1:-1],\n"
    "        \"source\": src,\n"
    "        \"has_file\": src != \"none\",\n"
    "    }\n"
)

CODE_CHECK_TEXT_CODE = (
    "def _flatten(x):\n"
    "    if x is None:\n"
    "        return \"\"\n"
    "    if isinstance(x, str):\n"
    "        return x\n"
    "    if isinstance(x, (list, tuple)):\n"
    "        return \"\\n\".join(_flatten(i) for i in x)\n"
    "    if isinstance(x, dict):\n"
    "        return str(x.get(\"text\") or x.get(\"content\") or \"\")\n"
    "    return str(x)\n"
    "\n"
    "def main(doc_text, var_text, merged) -> dict:\n"
    "    parts = [_flatten(doc_text), _flatten(var_text), _flatten(merged)]\n"
    "    best = max(parts, key=lambda s: len(s.strip()))\n"
    "    n = len(best.strip())\n"
    "    ok = n >= 10  # 少于 10 个字符基本就是空的 / 提取失败\n"
    "    if ok:\n"
    "        reason = \"提取正常\"\n"
    "    else:\n"
    "        reason = (\"正文为空（可能原因：扫描件/图片型 PDF 无文字层；\"\n"
    "                  \"DOCX 结构损坏导致解析失败；文档本身为空）\")\n"
    "    return {\"ok\": ok, \"reason\": reason, \"doc_chars\": n}\n"
)

CODE_FIND_KB_CODE = (
    "import json\n"
    "\n"
    "def main(body: str, filename: str) -> dict:\n"
    "    name = (filename or \"\").strip()\n"
    "    items = []\n"
    "    try:\n"
    "        data = json.loads(body or \"{}\")\n"
    "        if isinstance(data, dict) and isinstance(data.get(\"data\"), list):\n"
    "            items = data[\"data\"]\n"
    "    except Exception:\n"
    "        items = []\n"
    "    did = \"\"\n"
    "    for it in items:\n"
    "        if isinstance(it, dict) and str(it.get(\"name\") or \"\").strip() == name:\n"
    "            did = str(it.get(\"id\") or \"\")\n"
    "            break\n"
    "    return {\n"
    "        \"dataset_id\": did,\n"
    "        \"exists\": bool(did),\n"
    "        \"matched\": did[:8] if did else \"\",\n"
    "    }\n"
)

CODE_DECIDE_ID_CODE = (
    "import json\n"
    "\n"
    "def main(find_id: str, create_body: str, text: str) -> dict:\n"
    "    did = (find_id or \"\").strip()\n"
    "    how = \"复用已存在的知识库\"\n"
    "    if not did:\n"
    "        try:\n"
    "            data = json.loads(create_body or \"{}\")\n"
    "        except Exception:\n"
    "            data = {}\n"
    "        if isinstance(data, dict):\n"
    "            did = str(data.get(\"id\") or \"\")\n"
    "        how = \"新建知识库\" if did else \"未取到知识库ID（请看节点运行日志）\"\n"
    "    return {\n"
    "        \"dataset_id\": did,\n"
    "        \"how\": how,\n"
    "        \"text_esc\": json.dumps(text or \"\", ensure_ascii=False)[1:-1],\n"
    "    }\n"
)

CODE_PREP_SOURCE = (
    "import json\n"
    "def main(files: list, text: str) -> dict:\n"
    "    name = \"医学知识文件\"\n"
    "    try:\n"
    "        if files:\n"
    "            f = files[0]\n"
    "            if isinstance(f, dict):\n"
    "                name = f.get(\"name\") or f.get(\"filename\") or name\n"
    "    except Exception:\n"
    "        pass\n"
    "    name = str(name)\n"
    "    return {\n"
    "        \"filename\": name,\n"
    "        \"filename_esc\": json.dumps(name, ensure_ascii=False)[1:-1],\n"
    "        \"text_esc\": json.dumps(text or \"\", ensure_ascii=False)[1:-1],\n"
    "    }\n"
)


# 原始「知识检索」节点数据（从 Dify 导出的原始 graph 里抄来）
ORIG_KR_DATA = {
    "desc": "",
    "query_attachment_selector": [],
    "query_variable_selector": [START, "sys.query"],
    "retrieval_mode": "multiple",
    "title": "知识检索",
    "type": "knowledge-retrieval",
    "multiple_retrieval_config": {
        "top_k": 3,
        "reranking_enable": True,
        "reranking_model": {
            "provider": "langgenius/siliconflow/siliconflow",
            "model": "BAAI/bge-reranker-v2-m3",
        },
        "weights": {
            "keyword_setting": {"keyword_weight": 0},
            "weight_type": "customized",
            "vector_setting": {
                "embedding_provider_name": "langgenius/siliconflow/siliconflow",
                "vector_weight": 1,
                "embedding_model_name": "Pro/BAAI/bge-m3",
            },
        },
        "score_threshold": 0.5,
        "reranking_mode": "reranking_model",
    },
    "dataset_ids": list(DATASET_IDS),
    "selected": False,
}


# ---------------------------------------------------------------- 输出

def dump_steps(out_dir=None):
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "steps")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for s in sorted(STEP_TITLES):
        g = build_graph(s)
        p = os.path.join(out_dir, "step%d.json" % s)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False, indent=1)
        written.append((s, p, len(g["nodes"]), len(g["edges"])))
    return written


if __name__ == "__main__":
    for s, p, nn, ne in dump_steps():
        print("step%d  %-46s nodes=%-3d edges=%-3d  -> %s"
              % (s, STEP_TITLES[s], nn, ne, os.path.basename(p)))
