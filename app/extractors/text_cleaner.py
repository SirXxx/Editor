"""PDF 文本预处理器。

处理 PDF → 文本提取后的常见噪音，在送入 LLM 审稿前清洗文本。

主要处理：
1. 私用区（PUA）字符映射/清除
2. 页眉/页脚去重
3. 中文句子跨行截断拼接
4. 全角空格 / 多余空格规范化
5. 孤立表格标记清除（"续表"等）
6. 乱码符号过滤
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple, Optional

# ── PUA 字符映射表 ────────────────────────────────────────────────────────────
# 通过观察真实 PDF 提取结果建立，将私用区字符映射到最接近的可读字符
PUA_MAP: dict = {
    # 以下为本文档（知识产权讲义）中出现的 PUA 字符
    "\U001001b0": ".",    # 选项分隔符 "A．" 中的 ．
    "\U00100170": "·",   # 书名/标题中的间隔号
    "\U001001ba": "。",   # 句末句号
    # 通用 PUA 范围的常见映射
    "\uf0b7": "·",        # 常见 Symbol 字体 bullet
    "\uf06c": "●",
    "\uf0fc": "✓",
    "\uf0ae": "→",
    "\uf0e0": "→",
    "\uf020": " ",        # PUA 空格
}

# PUA Unicode 范围（快速检测）
PUA_RANGES = [
    (0xE000,  0xF8FF),   # BMP 私用区
    (0xF0000, 0xFFFFF),  # 补充私用区 A（本文档字符在这里）
    (0x100000, 0x10FFFF), # 补充私用区 B
]

# 行首/行尾的孤立噪音模式
_NOISE_LINE_PATTERNS = [
    re.compile(r'^续表\s*$'),           # 续表
    re.compile(r'^接上表\s*$'),
    re.compile(r'^\d+\s*$'),            # 孤立页码数字
    re.compile(r'^第\s*\d+\s*页\s*$'),  # "第X页"
    re.compile(r'^[·•○●▪▸◦\-—\s]+$'), # 纯符号行
    re.compile(r'^[\s\u3000]+$'),        # 纯空白/全角空格行
]

# 常见页眉/页脚特征词（连续出现在多页的行视为页眉页脚）
_HEADER_FOOTER_MARKERS = [
    "零基础过经济师",
    "第一篇",
    "第二篇",
    "第三篇",
    "第四篇",
]

# 供应商审校报告类前置页特征（应从正文审稿中剔除）
_NON_CONTENT_FRONT_MARKERS = [
    "审校报告",
    "审校结果概览",
    "审校结果高亮颜色说明",
    "客服咨询电话",
    "如需试用请扫描二维码",
    "znsj.founderss.cn",
    "可疑内容",
]


def _is_pua(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in PUA_RANGES)


def clean_pua(text: str, stats: Optional[dict] = None) -> str:
    """替换/删除私用区字符。

    重要：未知 PUA 字符不再静默删除（出版字体可能用 PUA 编码真实汉字，
    直接删除会无声丢字）。改为替换为可见占位符 ``□`` 并计数，便于上层
    在丢字过多时发出告警，提示“疑似字体编码异常，需人工核对原文”。
    """
    result = []
    for ch in text:
        if ch in PUA_MAP:
            result.append(PUA_MAP[ch])
        elif _is_pua(ch):
            # Unknown PUA: 保留可见占位符而非静默删除，并计数
            result.append("□")
            if stats is not None:
                stats["pua_dropped"] = stats.get("pua_dropped", 0) + 1
        else:
            result.append(ch)
    return "".join(result)


def normalize_whitespace(text: str) -> str:
    """全角空格 → 普通空格；多个空格合并；行内多余空白清理。"""
    text = text.replace("\u3000", " ")   # ideographic space
    text = text.replace("\u00a0", " ")   # non-breaking space
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def join_broken_lines(lines: List[str]) -> List[str]:
    """智能合并被换行截断的中文句子。

    判断规则：
    - 当前行不以句号/问号/感叹号/冒号/分号结尾
    - 当前行末尾是中文字符（说明句子未结束）
    - 下一行不以标题符号（一二三、【】等）开头
    """
    if not lines:
        return lines
    result = [lines[0]]
    for line in lines[1:]:
        prev = result[-1].rstrip()
        curr = line.strip()
        if not curr:
            result.append(curr)
            continue
        # Decision: should curr be joined to prev?
        should_join = (
            len(prev) > 0
            and len(curr) > 0
            # prev ends with a Chinese character (incomplete sentence)
            and _is_cjk(prev[-1])
            # prev doesn't end with terminal punctuation
            and prev[-1] not in "。？！；：…\n.?!;:"
            # curr doesn't start with a new paragraph/section marker
            and not re.match(r'^[一二三四五六七八九十（(【\[①②③④⑤⑥⑦⑧⑨⑩\d]', curr)
            and not re.match(r'^[A-Z（(][\U001001b0\.\．、\s]', curr)  # option letter
        )
        if should_join:
            result[-1] = prev + curr
        else:
            result.append(curr)
    return result


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or   # Extension A
            0x20000 <= cp <= 0x2A6DF)   # Extension B


def remove_noise_lines(lines: List[str]) -> List[str]:
    """过滤孤立的噪音行。"""
    result = []
    for line in lines:
        stripped = line.strip()
        is_noise = any(p.match(stripped) for p in _NOISE_LINE_PATTERNS)
        if not is_noise:
            result.append(line)
    return result


def remove_repeated_headers(pages_text: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """识别并去除重复的页眉/页脚行。

    策略：统计每行在多少页中出现，出现频率 > 30% 的短行视为页眉/页脚。
    """
    from collections import Counter
    # Count line frequency
    line_freq: Counter = Counter()
    for _, text in pages_text:
        seen = set()
        for line in text.split("\n"):
            s = line.strip()
            if s and len(s) < 60:  # Only short lines can be headers
                if s not in seen:
                    line_freq[s] += 1
                    seen.add(s)
    total_pages = len(pages_text)
    threshold = max(2, int(total_pages * 0.25))  # appears in ≥25% of pages
    header_lines = {line for line, cnt in line_freq.items() if cnt >= threshold}
    # Also mark lines containing known header markers
    for _, text in pages_text:
        for line in text.split("\n"):
            s = line.strip()
            if any(marker in s for marker in _HEADER_FOOTER_MARKERS) and len(s) < 80:
                header_lines.add(s)

    result = []
    for page_num, text in pages_text:
        clean_lines = []
        for line in text.split("\n"):
            if line.strip() not in header_lines:
                clean_lines.append(line)
        result.append((page_num, "\n".join(clean_lines)))
    return result


def preprocess_page(page_text: str, stats: Optional[dict] = None) -> str:
    """对单页文本执行完整的预处理流水线。"""
    # 1. PUA characters
    text = clean_pua(page_text, stats=stats)
    # 2. Whitespace
    text = normalize_whitespace(text)
    # 3. Per-line cleaning
    lines = text.split("\n")
    lines = remove_noise_lines(lines)
    lines = join_broken_lines(lines)
    return "\n".join(l for l in lines if l.strip())


def preprocess_pages(
    pages_text: List[Tuple[int, str]],
    stats: Optional[dict] = None,
) -> List[Tuple[int, str]]:
    """对多页文本执行包含页眉去重的完整预处理。

    stats（可选）：传入 dict 时会累计 ``pua_dropped`` 等指标，供上层告警。
    """
    # First pass: remove repeated headers across pages
    pages_text = remove_repeated_headers(pages_text)
    # Second pass: per-page cleanup
    result = []
    for page_num, text in pages_text:
        clean = preprocess_page(text, stats=stats)
        result.append((page_num, clean))
    return result


def strip_non_content_front_pages(
    pages_text: List[Tuple[int, str]],
) -> Tuple[List[Tuple[int, str]], List[int]]:
    """剔除文档开头的非正文页（如第三方审校封面/概览页）。

    仅处理“连续前置页”，避免误删正文中偶发出现的相似词。
    返回：(过滤后的页列表, 被跳过的页码列表)
    """
    if not pages_text:
        return pages_text, []

    skipped: List[int] = []
    report_like_seen = False

    for page_num, text in pages_text:
        compact = re.sub(r"\s+", "", text or "")
        marker_hits = sum(1 for m in _NON_CONTENT_FRONT_MARKERS if m in compact)
        is_report_like = marker_hits >= 2
        is_blank = len(compact) < 20
        should_skip = is_report_like or (report_like_seen and is_blank)

        if should_skip:
            skipped.append(page_num)
            if is_report_like:
                report_like_seen = True
            continue
        break

    # 防御：若全部被识别为可跳过，回退为不跳过，避免空文本
    if skipped and len(skipped) < len(pages_text):
        return pages_text[len(skipped):], skipped
    return pages_text, []


def preprocess_text(text: str) -> str:
    """对已合并的全文执行预处理（无页眉去重）。"""
    text = clean_pua(text)
    text = normalize_whitespace(text)
    lines = text.split("\n")
    lines = remove_noise_lines(lines)
    lines = join_broken_lines(lines)
    return "\n".join(l for l in lines if l.strip())


def page_text_layout_aware(page) -> str:
    """版面感知的取文：用 block 坐标重排，缓解双栏/表格乱序。

    fitz 默认 ``get_text("text")`` 按内部阅读流输出，双栏排版常左右交错，
    导致句子被打乱、产生大量假阳性。此函数：
    1. 取 ``get_text("blocks")``（含坐标 x0,y0,x1,y1）。
    2. 用 block 中点 x 的分布判断是否双栏：若明显分成左右两簇，则
       左栏自上而下、再右栏自上而下输出；否则按 (y, x) 单栏排序。

    任何异常都回退到普通 ``get_text("text")``，保证健壮。
    """
    try:
        blocks = page.get_text("blocks")
    except Exception:
        try:
            return page.get_text("text")
        except Exception:
            return ""

    # blocks: (x0, y0, x1, y1, text, block_no, block_type)
    text_blocks = [b for b in blocks if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()]
    if not text_blocks:
        try:
            return page.get_text("text")
        except Exception:
            return ""

    try:
        page_width = float(page.rect.width)
    except Exception:
        page_width = 0.0

    centers = sorted(((b[0] + b[2]) / 2.0) for b in text_blocks)
    two_column = False
    split_x = 0.0
    if page_width > 0 and len(text_blocks) >= 4:
        mid = page_width / 2.0
        left = [c for c in centers if c < mid]
        right = [c for c in centers if c >= mid]
        # 双栏判定：两侧都有足够 block，且各自中心远离页面中线
        if len(left) >= 2 and len(right) >= 2:
            avg_left = sum(left) / len(left)
            avg_right = sum(right) / len(right)
            if (mid - avg_left) > page_width * 0.12 and (avg_right - mid) > page_width * 0.12:
                two_column = True
                split_x = mid

    if two_column:
        left_blocks = [b for b in text_blocks if (b[0] + b[2]) / 2.0 < split_x]
        right_blocks = [b for b in text_blocks if (b[0] + b[2]) / 2.0 >= split_x]
        left_blocks.sort(key=lambda b: (round(b[1], 1), b[0]))
        right_blocks.sort(key=lambda b: (round(b[1], 1), b[0]))
        ordered = left_blocks + right_blocks
    else:
        ordered = sorted(text_blocks, key=lambda b: (round(b[1], 1), b[0]))

    return "\n".join(b[4].strip() for b in ordered if b[4].strip())
