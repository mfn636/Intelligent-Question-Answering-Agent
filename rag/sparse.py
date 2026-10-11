"""
rag/sparse.py

轻量 BM25（关键词/稀疏检索），用于混合检索的"字面"一路。
中文按字符 bigram 切分，英文/数字按词切分，无第三方依赖。
"""

import math
import re
from collections import Counter

_ASCII = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str):
    text = (text or "").lower()
    tokens = _ASCII.findall(text)
    cjk = _CJK.findall(text)
    tokens += cjk                                   # 单字
    tokens += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]  # 中文 bigram
    return tokens


class BM25:
    def __init__(self, docs, k1: float = 1.5, b: float = 0.75):
        """docs: list of (id, text, payload)"""
        self.ids, self.payloads, self.tokens = [], [], []
        self.df = Counter()
        for cid, text, payload in docs:
            toks = tokenize(text)
            self.ids.append(cid)
            self.payloads.append(payload)
            self.tokens.append(toks)
            for t in set(toks):
                self.df[t] += 1
        self.N = len(docs)
        self.avgdl = sum(len(t) for t in self.tokens) / max(1, self.N)
        self.k1, self.b = k1, b

    def search(self, query: str, top_k: int = 20):
        q = tokenize(query)
        scored = []
        for i, toks in enumerate(self.tokens):
            tf = Counter(toks)
            dl = len(toks)
            s = 0.0
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (self.N - self.df[term] + 0.5) / (self.df[term] + 0.5))
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"id": self.ids[i], "payload": self.payloads[i], "score": s}
                for s, i in scored[:top_k]]
