"""自定义审稿规则管理器。

规则文件：data/workspace/review_rules.json
规则在 ReviewOrchestrator 调用 LLM 时动态注入到 system prompt 末尾。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import REVIEW_RULES_PATH

RULES_PATH = REVIEW_RULES_PATH


def _atomic_write_text(path: Path, content: str) -> None:
    """原子写：先写临时文件再 os.replace，避免并发/中断导致文件损坏（O5）。"""
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

# ── 内置规则定义（每条规则有唯一 id、名称、说明、适用维度、默认启用状态）────────
# 分三组：
#   Group A - PDF 提取噪音（默认全开，这类问题是 PDF→文本的技术伪像，不是真正的错误）
#   Group B - 格式主观偏好（用户可按需开关）
#   Group C - 内容检查偏好（用户可按需开关）

BUILTIN_RULE_DEFS: List[Dict[str, Any]] = [

    # ════════════════════════════════════════════════════════════════
    # A. PDF 提取噪音 — 默认全部开启
    #    这些都是 PDF→文本转换工具引入的技术伪像，不是文档本身的问题
    # ════════════════════════════════════════════════════════════════
    {
        "id": "no_fullwidth_halfwidth",
        "group": "PDF噪音",
        "name": "忽略全角/半角混用",
        "description": "PDF 提取常将全角数字（１２３）、字母（ＡＢＣ）遗留在文本中，非原文问题",
        "dimension": ["spelling"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查全角/半角数字或字母的差异，忽略全角数字（如１２３）与半角（如123）的混用，这是PDF提取工具的格式副产品。",
        "enabled": True,
    },
    {
        "id": "no_punctuation_style",
        "group": "PDF噪音",
        "name": "忽略中英文标点混用",
        "description": "PDF 提取常混淆中英文标点：句号（。.）、逗号（，,）、冒号（：:）等",
        "dimension": ["spelling", "grammar"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查中英文标点符号的混用，包括句号（。与.）、逗号（，与,）、冒号（：与:）、分号（；与;）、感叹号（！与!）、问号（？与?）的全角/半角形式，这是PDF提取的格式副产品。",
        "enabled": True,
    },
    {
        "id": "no_space_issues",
        "group": "PDF噪音",
        "name": "忽略空格相关问题",
        "description": "PDF 提取会在中英文之间插入多余空格，或在换行处丢失空格，均属技术噪音",
        "dimension": ["spelling", "grammar"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查任何空格相关问题，包括：中英文之间的空格、词语间多余空格、换行产生的空格断词，这些都是PDF提取的常见副产品。",
        "enabled": True,
    },
    {
        "id": "no_linebreak_artifacts",
        "group": "PDF噪音",
        "name": "忽略换行断词问题",
        "description": "PDF 提取常在原本连续的词中插入换行，造成词语被截断，如'知识产\\n权'",
        "dimension": ["spelling", "grammar"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将因换行导致的词语截断标记为错误，PDF提取常在词语中间插入换行符。",
        "enabled": True,
    },
    {
        "id": "no_garbled_chars",
        "group": "PDF噪音",
        "name": "忽略乱码/特殊字符",
        "description": "PDF 提取常产生无法识别的Unicode私用区字符、框线字符、公式残留符号等",
        "dimension": ["spelling", "grammar"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将无法识别的特殊字符、框线符号、Unicode私用区字符（如的□■▪◦）标记为问题，这是PDF提取的技术噪音。",
        "enabled": True,
    },
    {
        "id": "no_header_footer_noise",
        "group": "PDF噪音",
        "name": "忽略页眉页脚混入文本",
        "description": "PDF 提取常将页码、页眉（如书名）、页脚混入正文段落，不属于文档本身的问题",
        "dimension": ["spelling", "grammar", "style"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将疑似页眉、页脚、页码编号或书名重复出现标记为格式错误，这是PDF提取的副产品。遇到孤立的数字或书名片段直接忽略。",
        "enabled": True,
    },
    {
        "id": "no_table_list_symbols",
        "group": "PDF噪音",
        "name": "忽略表格/列表符号格式",
        "description": "PDF 提取中表格分隔符、列表符号（①②③、A. B.）格式不统一，不视为错误",
        "dimension": ["spelling"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查列表符号（①②③ vs A. B. C. vs 1. 2. 3.）的一致性问题，也不要检查表格分隔符的格式，这是PDF提取的常见差异。",
        "enabled": True,
    },
    {
        "id": "no_ellipsis_style",
        "group": "PDF噪音",
        "name": "忽略省略号样式差异",
        "description": "PDF 提取常将省略号（……）转为三个句点（...），视为等同",
        "dimension": ["spelling"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查省略号的样式差异，将'...'和'……'视为等同，不标记为格式错误。",
        "enabled": True,
    },
    {
        "id": "no_quote_style",
        "group": "PDF噪音",
        "name": "忽略引号样式差异",
        "description": "PDF 提取常混淆弯引号（''""）和直引号（''\"\"），视为等同",
        "dimension": ["spelling"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查引号的样式差异（弯引号与直引号的区别），PDF提取工具常造成引号类型改变。",
        "enabled": True,
    },
    {
        "id": "no_dash_style",
        "group": "PDF噪音",
        "name": "忽略连字符/破折号差异",
        "description": "PDF 提取常混淆连字符(-)、破折号(—)、波浪线(～)，以及表格连接符（如Ｇ代替-）",
        "dimension": ["spelling"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查连字符、破折号、波浪线的类型差异（- vs — vs ～ vs Ｇ），PDF提取常造成这些符号相互替代。",
        "enabled": True,
    },
    {
        "id": "no_superscript_footnote",
        "group": "PDF噪音",
        "name": "忽略上标/脚注编号",
        "description": "PDF 提取常将脚注编号、文献引用上标（如[1][2]、①）混入正文，不视为语法错误",
        "dimension": ["grammar"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将脚注编号、文献引用标记（如[1]、①、上标数字）出现在正文中标记为语法错误，这是PDF提取的正常现象。",
        "enabled": True,
    },
    {
        "id": "no_number_format",
        "group": "PDF噪音",
        "name": "忽略数字格式细节",
        "description": "忽略千分位、小数点格式、数字与单位之间的空格等排版细节",
        "dimension": ["spelling"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要检查数字格式规范细节，如千分位写法、小数点形式、数字与单位之间的空格等。",
        "enabled": True,
    },

    # ════════════════════════════════════════════════════════════════
    # B. 格式主观偏好 — 默认关闭，用户可按需开启
    # ════════════════════════════════════════════════════════════════
    {
        "id": "allow_informal_tone",
        "group": "风格偏好",
        "name": "允许口语化表达",
        "description": "不将口语化、非正式表达标记为问题（适合培训材料、科普文档等）",
        "dimension": ["style"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将口语化表达或非正式语气标记为问题，允许文档使用口语化风格。",
        "enabled": False,
    },
    {
        "id": "no_abbreviation_fullname",
        "group": "风格偏好",
        "name": "不要求缩写给出全称",
        "description": "忽略缩写首次出现未给全称的问题（如AI无需写出全称）",
        "dimension": ["terminology"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要要求缩写首次出现必须给出全称，允许直接使用缩写。",
        "enabled": False,
    },
    {
        "id": "no_passive_voice",
        "group": "风格偏好",
        "name": "允许被动句",
        "description": "不将被动语态（如'由……组成'）标记为风格问题",
        "dimension": ["style"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将被动语态或被动句式标记为风格问题。",
        "enabled": False,
    },
    {
        "id": "no_long_sentence",
        "group": "风格偏好",
        "name": "允许长句",
        "description": "不将超过60字的长句标记为问题（法律/学术文档常有规范的长句）",
        "dimension": ["style"],
        "type": "disable",
        "prompt_instruction": "【忽略规则】不要将句子过长标记为问题，允许文档存在规范的长句。",
        "enabled": False,
    },
]


class ReviewRulesManager:
    """管理用户自定义审稿规则，并生成注入到 prompt 的指令文本。"""

    def __init__(self) -> None:
        self._rules: List[Dict[str, Any]] = []
        self._custom_text: str = ""
        self.load()

    def load(self) -> None:
        """从文件加载规则，合并内置定义。"""
        saved: Dict[str, Any] = {}
        if RULES_PATH.exists():
            try:
                saved = json.loads(RULES_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass

        saved_states: Dict[str, bool] = saved.get("rule_states", {})
        self._rules = []
        for defn in BUILTIN_RULE_DEFS:
            rule = dict(defn)
            if defn["id"] in saved_states:
                rule["enabled"] = saved_states[defn["id"]]
            self._rules.append(rule)

        # User-defined custom rules
        for custom in saved.get("custom_rules", []):
            self._rules.append(custom)

        self._custom_text = saved.get("custom_text", "")

    def save(self) -> None:
        RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        builtin_ids = {r["id"] for r in BUILTIN_RULE_DEFS}
        rule_states = {r["id"]: r["enabled"] for r in self._rules if r["id"] in builtin_ids}
        custom_rules = [r for r in self._rules if r["id"] not in builtin_ids]
        _atomic_write_text(RULES_PATH, json.dumps({
            "rule_states": rule_states,
            "custom_rules": custom_rules,
            "custom_text": self._custom_text,
        }, ensure_ascii=False, indent=2))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rules": self._rules,
            "custom_text": self._custom_text,
        }

    def get_prompt_suffix(self, dimension: str) -> str:
        """生成针对特定维度的 prompt 追加指令。"""
        parts: List[str] = []
        for rule in self._rules:
            if not rule.get("enabled"):
                continue
            dims = rule.get("dimension", [])
            if dimension in dims or "*" in dims:
                instr = rule.get("prompt_instruction", "")
                if instr:
                    parts.append(instr)
        if self._custom_text.strip():
            parts.append(f"【用户补充规则】\n{self._custom_text.strip()}")
        if not parts:
            return ""
        return "\n\n--- 以下为用户自定义忽略规则，请严格遵守 ---\n" + "\n".join(parts)

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> bool:
        for rule in self._rules:
            if rule["id"] == rule_id:
                rule["enabled"] = enabled
                return True
        return False

    def add_custom_rule(self, rule: Dict[str, Any]) -> None:
        self._rules.append(rule)

    def set_custom_text(self, text: str) -> None:
        self._custom_text = text

    @property
    def rules(self) -> List[Dict[str, Any]]:
        return self._rules

    @property
    def custom_text(self) -> str:
        return self._custom_text


# Global singleton
_manager: Optional[ReviewRulesManager] = None


def get_rules_manager() -> ReviewRulesManager:
    global _manager
    if _manager is None:
        _manager = ReviewRulesManager()
    return _manager
