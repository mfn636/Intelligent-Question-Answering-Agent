"""
rag/chunker.py

分块引擎（领域无关）：长文本 → 按标题 / 段落切块。
商品 / FAQ / 指南的分块组织放在 rag/ecommerce_chunks.py。
"""


def split_by_headings(content, max_len=400, overlap=60, heading_mark="## "):
    """按 `## ` 标题切成小节块；小节超长则按段落细切，并把标题前置保证上下文。"""
    sections, cur = [], []
    for line in content.splitlines():
        if line.lstrip().startswith(heading_mark) and cur:
            sections.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())

    out = []
    for sec in sections:
        if not sec:
            continue
        if len(sec) <= max_len:
            out.append(sec)
            continue
        heading = sec.splitlines()[0] if sec.splitlines() else ""
        for sub in chunk_text(sec, max_len=max_len, overlap=overlap):
            out.append(sub if sub.startswith(heading) else f"{heading}\n{sub}")
    return out


def chunk_text(text, max_len=400, overlap=60):
    """按行(段落)聚合成长度 <= max_len 的块，块间保留 overlap 字符。"""
    paras = [p.strip() for p in text.splitlines() if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 1 > max_len:
            chunks.append(cur)
            cur = (cur[-overlap:] if overlap else "")
        cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks
