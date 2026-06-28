"""敏感词与合规扫描器（确定性，不依赖 LLM）。

设计动机
--------
政治/敏感内容审查不能仅依赖在线大模型，原因有三：

1. 召回不可靠：托管大模型出于自身安全策略，往往会回避“复述”敏感词，
   导致本应被标记的内容反而被模型静默跳过，造成漏检。
2. 不可审计：模型输出不稳定、不可复现，而出版社的政治把关需要可追溯、
   可复核的命中记录。
3. 词库需由编辑维护：不同选题、不同时期的敏感词清单不同，必须支持编辑
   自行增删，而不是固化在模型里。

因此本模块提供一个基于「词库 + 正则启发式」的确定性扫描器：
- 命中结果稳定、可复现、带页码/行号定位；
- 全部命中标注 needs_review=True，仅作“提请人工终审”，不替代人工判断；
- 词库以 JSON 形式存放于 data/workspace/sensitive_lexicon.json，可由编辑维护，
  缺省时使用内置基础词库。
"""
from __future__ import annotations

import json
import re
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import SENSITIVE_LEXICON_PATH

LEXICON_PATH = SENSITIVE_LEXICON_PATH


def _atomic_write_text(path: Path, content: str) -> None:
    """原子写：先写临时文件再 os.replace，避免并发/中断损坏文件（O5）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

# ── 内置基础词库 ──────────────────────────────────────────────────────────────
# 每个类别：
#   dimension      命中后归入的审稿维度（politics / sensitive）
#   severity       默认严重度
#   advice         给编辑的处理建议
#   terms          需要命中的词（精确包含匹配）
#
# 说明：这是“起步词库”，真实出版场景应由编辑结合选题持续扩充。
BUILTIN_LEXICON: Dict[str, Dict[str, Any]] = {
    "separatism": {
        "dimension": "politics",
        "severity": "high",
        "advice": "涉及国家主权与领土完整的高风险表述，须核对是否为引用、批驳语境，并对照权威口径处理。",
        "terms": [
            "台独", "港独", "藏独", "疆独", "蒙独",
            "一中一台", "两个中国", "台湾国", "台湾共和国",
        ],
    },
    "sovereignty_wording": {
        "dimension": "politics",
        "severity": "medium",
        "advice": "涉台/涉港澳表述需核对官方规范用语（如不得将台湾、香港、澳门与“国家”并列）。",
        "terms": [
            "中国大陆和台湾", "大陆与台湾", "中国和香港", "中国及香港",
            "中国和澳门", "中国与台湾", "回归祖国怀抱",
        ],
    },
    "leader_reference": {
        "dimension": "politics",
        "severity": "medium",
        "advice": "涉及党和国家领导人称谓/职务/讲话引用，须逐字核对姓名、职务、时间与原文表述。",
        "terms": [
            "总书记", "国家主席", "总理", "重要讲话", "重要指示", "重要论述",
        ],
    },
    "ad_absolute": {
        "dimension": "sensitive",
        "severity": "medium",
        "advice": "《广告法》第九条禁用的极限用语，正文/宣传语中应删除或改写。",
        "terms": [
            "最佳", "最好", "最高级", "最低价", "最优", "最先进",
            "第一品牌", "国家级", "世界级", "顶级", "极品", "绝无仅有",
            "独一无二", "首选", "唯一", "史无前例", "万能", "百分百",
        ],
    },
    "vulgar_superstition": {
        "dimension": "sensitive",
        "severity": "low",
        "advice": "疑似低俗/封建迷信用语，请核对语境是否恰当。",
        "terms": [
            "风水宝地", "算命", "看相", "转运", "辟邪", "招财进宝", "包治百病",
        ],
    },
    "forbidden_orientation": {
        "dimension": "sensitive",
        "severity": "high",
        "advice": "疑似违法违规导向词，须核对语境并依规处理。",
        "terms": [
            "赌博", "毒品", "枪支", "传销",
        ],
    },
}

# ── 正则启发式规则 ────────────────────────────────────────────────────────────
# 用于无法靠单词命中、需要上下文模式判断的场景。
# pattern 命中后给出 needs_review 提示。
_HEURISTICS: List[Dict[str, Any]] = [
    {
        "id": "taiwan_country_collocation",
        "dimension": "politics",
        "severity": "high",
        "regex": re.compile(r"台湾[^。；，]{0,6}(国家|各国|外国)"),
        "advice": "“台湾”与“国家/各国/外国”近距离搭配，存在将台湾表述为国家的风险，须核对。",
    },
    {
        "id": "map_boundary",
        "dimension": "politics",
        "severity": "medium",
        "regex": re.compile(r"(地图|疆域|国界|版图)"),
        "advice": "出现地图/疆域/国界/版图相关表述，须核对是否完整准确（如南海诸岛、藏南等）。",
    },
]

_CONTEXT_RADIUS = 16


# ── 参考文献 / 版权合规启发式 ────────────────────────────────────────────────
# 这些规则针对“引用规范、参考文献完整性、图表来源、网址”等可模式化的合规问题，
# 命中后归入 copyright 维度，全部 needs_review，提请人工核对。
_COPYRIGHT_HEURISTICS: List[Dict[str, Any]] = [
    {
        "id": "incomplete_url",
        "severity": "low",
        # 出现 http(s) 但疑似被截断（结尾是省略号/连字符/空白后即结束）
        "regex": re.compile(r"https?://[^\s，。；）)]{0,80}(?:\.{2,}|…|-)\s"),
        "advice": "网址疑似不完整或被截断，请核对完整 URL 并补充访问日期。",
    },
    {
        "id": "citation_missing_year",
        "severity": "medium",
        # 中文“（参见/引自/转引自/见 XXX）”但括号内无4位年份
        "regex": re.compile(r"(?:参见|引自|转引自|详见)[^（(]{0,12}(?![^）)]*\d{4})[（(][^）)]{0,30}[）)]"),
        "advice": "引用标注疑似缺少年份等著录要素，请按规范补全（作者、年份、出处）。",
    },
    {
        "id": "figure_no_source",
        "severity": "medium",
        # 图/表标题但同段未出现“来源/资料/引自/摄/绘/版权”等字样
        "regex": re.compile(r"(?:图|表)\s*\d+[\-－.]?\d*[^\n]{0,40}"),
        "advice": "图表疑似未标注来源/版权，若引自第三方请注明来源并核实授权。",
        "negative": re.compile(r"来源|资料来源|引自|改绘|摄|绘制|版权|本书|作者自"),
    },
    {
        "id": "quote_no_attribution",
        "severity": "medium",
        # 出现成对中文引号包裹的较长内容，疑似直接引用
        "regex": re.compile(r"“[^”]{20,}”"),
        "advice": "疑似整段引用，请核实是否标注出处并在合理使用范围内，必要时取得授权。",
        "negative": re.compile(r"来源|引自|出自|—{1,2}|――|参见"),
    },
]


def _load_lexicon() -> Dict[str, Dict[str, Any]]:
    """加载词库：内置为基础，用户 JSON 覆盖/扩充同名类别的 terms。"""
    lexicon = {k: {**v, "terms": list(v["terms"])} for k, v in BUILTIN_LEXICON.items()}
    if LEXICON_PATH.exists():
        try:
            user = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
        except Exception:
            user = {}
        for cat, cfg in (user.get("categories") or {}).items():
            if cat in lexicon:
                merged = set(lexicon[cat]["terms"]) | set(cfg.get("terms", []))
                lexicon[cat]["terms"] = sorted(merged)
                for key in ("severity", "advice", "dimension"):
                    if cfg.get(key):
                        lexicon[cat][key] = cfg[key]
            else:
                lexicon[cat] = {
                    "dimension": cfg.get("dimension", "sensitive"),
                    "severity": cfg.get("severity", "medium"),
                    "advice": cfg.get("advice", "命中自定义敏感词，请人工复核。"),
                    "terms": list(cfg.get("terms", [])),
                }
    return lexicon


def ensure_lexicon_file() -> Path:
    """若词库文件不存在，写出内置词库作为可编辑模板。"""
    if not LEXICON_PATH.exists():
        LEXICON_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "categories": {
                cat: {
                    "dimension": cfg["dimension"],
                    "severity": cfg["severity"],
                    "advice": cfg["advice"],
                    "terms": cfg["terms"],
                }
                for cat, cfg in BUILTIN_LEXICON.items()
            }
        }
        LEXICON_PATH.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(LEXICON_PATH,
                           json.dumps(payload, ensure_ascii=False, indent=2))
    return LEXICON_PATH


def get_lexicon_dict() -> Dict[str, Any]:
    """返回当前生效词库（供 API/前端编辑）。"""
    lex = _load_lexicon()
    return {
        "categories": {
            cat: {
                "dimension": cfg["dimension"],
                "severity": cfg["severity"],
                "advice": cfg["advice"],
                "terms": cfg["terms"],
            }
            for cat, cfg in lex.items()
        }
    }


def save_lexicon_dict(data: Dict[str, Any]) -> Path:
    """保存编辑后的词库。"""
    categories = data.get("categories", data)
    _atomic_write_text(LEXICON_PATH,
                       json.dumps({"categories": categories}, ensure_ascii=False, indent=2))
    return LEXICON_PATH


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _snippet(text: str, start: int, end: int) -> str:
    a = max(0, start - _CONTEXT_RADIUS)
    b = min(len(text), end + _CONTEXT_RADIUS)
    return text[a:b].replace("\n", " ").strip()


def scan_pages(
    pages: List[Tuple[int, str]],
    dimensions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """对每页文本执行确定性敏感词/合规扫描。

    Parameters
    ----------
    pages:       [(page_num, page_text), ...]
    dimensions:  仅返回这些维度的命中（如 ['politics','sensitive']）。
                 None 表示返回全部。

    Returns
    -------
    问题字典列表，字段与 LLM 维度产出的 issue 对齐，并带页码/行号。
    """
    lexicon = _load_lexicon()
    want = set(dimensions) if dimensions else None
    issues: List[Dict[str, Any]] = []

    for page_num, page_text in pages:
        if not page_text:
            continue

        # 词库精确命中
        for cat, cfg in lexicon.items():
            dim = cfg.get("dimension", "sensitive")
            if want is not None and dim not in want:
                continue
            for term in cfg.get("terms", []):
                if not term:
                    continue
                start = 0
                while True:
                    idx = page_text.find(term, start)
                    if idx == -1:
                        break
                    issues.append({
                        "issue_type": dim,
                        "dimension": dim,
                        "severity": cfg.get("severity", "medium"),
                        "page": page_num,
                        "line": _line_of(page_text, idx),
                        "original": _snippet(page_text, idx, idx + len(term)),
                        "suggestion": cfg.get("advice", "请人工复核。"),
                        "reason": f"命中敏感词库【{cat}】：{term}",
                        "confidence": 0.6,
                        "needs_review": True,
                    })
                    start = idx + len(term)

        # 正则启发式命中
        for rule in _HEURISTICS:
            dim = rule["dimension"]
            if want is not None and dim not in want:
                continue
            for m in rule["regex"].finditer(page_text):
                issues.append({
                    "issue_type": dim,
                    "dimension": dim,
                    "severity": rule.get("severity", "medium"),
                    "page": page_num,
                    "line": _line_of(page_text, m.start()),
                    "original": _snippet(page_text, m.start(), m.end()),
                    "suggestion": rule.get("advice", "请人工复核。"),
                    "reason": f"启发式规则命中：{rule['id']}",
                    "confidence": 0.5,
                    "needs_review": True,
                })

        # 版权 / 参考文献合规启发式
        if want is None or "copyright" in want:
            for rule in _COPYRIGHT_HEURISTICS:
                neg = rule.get("negative")
                for m in rule["regex"].finditer(page_text):
                    ctx = _snippet(page_text, m.start(), m.end())
                    if neg is not None and neg.search(ctx):
                        continue
                    issues.append({
                        "issue_type": "copyright",
                        "dimension": "copyright",
                        "severity": rule.get("severity", "medium"),
                        "page": page_num,
                        "line": _line_of(page_text, m.start()),
                        "original": ctx,
                        "suggestion": rule.get("advice", "请人工复核。"),
                        "reason": f"版权/引用启发式命中：{rule['id']}",
                        "confidence": 0.45,
                        "needs_review": True,
                    })

    return issues
