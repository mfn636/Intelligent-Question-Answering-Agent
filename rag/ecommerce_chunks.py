"""
rag/ecommerce_chunks.py

电商知识分块：FAQ / 长文档（指南 · 政策 · 帮助）。
通用分块能力（按标题 / 段落切）复用 rag/chunker.py。
"""

from rag.chunker import split_by_headings


def chunk_faq(faq_items):
    """FAQ：一条一个 chunk；payload 存 question/answer 以便还原。"""
    chunks = []
    for i, faq in enumerate(faq_items):
        chunks.append({
            "id": f"faq-{i}",
            "text": f"{faq['question']}\n{faq['answer']}",
            "payload": {
                "source": "faq",
                "category": faq["category"],
                "question": faq["question"],
                "answer": faq["answer"],
            },
        })
    return chunks


def chunk_guides(guides, max_len=400, overlap=60):
    """长文档：标题感知分块——按 `##` 小节切；超长小节再按段落细切（标题前置）。"""
    chunks = []
    for g in guides:
        title = g.get("title", "")
        for j, sec in enumerate(split_by_headings(g.get("content", ""), max_len, overlap)):
            chunks.append({
                "id": f"guide-{g['id']}-{j}",
                "text": f"{title}｜{sec}",
                "payload": {
                    "source": g.get("source", "guide"),
                    "doc_id": g["id"],
                    "title": title,
                    "category": g.get("category", ""),
                },
            })
    return chunks
