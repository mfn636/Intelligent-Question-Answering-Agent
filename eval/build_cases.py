"""Reproducibly create draft labels from the bundled data, never from model answers.

Run explicitly to regenerate; generated labels still need independent human review.
"""
from collections import Counter
from pathlib import Path

from eval.common import ROOT, append_jsonl, digest, text_file_hash, write_json
from eval.schema import load_cases
from storage.json_loader import load_ecommerce_data, load_faq


def build_cases():
    data = load_ecommerce_data()
    products = {p["id"]: p for p in data.products}
    stocks = {p["product_id"]: p["stock"] for p in data.inventory}
    cases = []

    def turn(user, requirement, *, source="user_request", expected=None, plans=None, routes=None, no_tools=False, docs=None):
        return {"user": user, "checks": [{"id": "goal", "requirement": requirement, "source": source, "expected": expected}],
                "acceptable_tool_plans": plans or [], "allowed_routes": routes or ["direct", "workflow", "agent"],
                "no_tools": no_tools, "expected_doc_ids": docs or []}

    def step(name, **args):
        return {"tool": name, "args": args}

    def detail(pid, user=None, fields=("price",), routes=None):
        facts = {key: products[pid][key] for key in fields}
        return turn(user or f"{pid} 的价格是多少？", f"正确对应 {pid} 的事实 {facts}；未提供的信息不猜测。",
                    source=f"data/products.json#{pid}", expected=facts,
                    plans=[[step("get_product_detail", product_id=pid)]], routes=routes or (["workflow"] if fields == ("price",) else ["agent"]))

    def inventory(pid, user=None):
        return turn(user or f"{pid} 有货吗", f"正确说明 {pid} 库存为 {stocks[pid]}，零库存不得说有货。",
                    source=f"data/inventory.json#{pid}", expected={"stock": stocks[pid]},
                    plans=[[step("check_inventory", product_id=pid)]], routes=["workflow"] if user is None or user == "它有货吗" else ["workflow", "agent"])

    def add(category, family, turns, *, split="dev", pilot=False, tags=None, fault=None, summaries=0):
        index = sum(c["category"] == category for c in cases) + 1
        cases.append({"id": f"{category}-{index:02d}", "split": split, "family": family, "category": category,
                      "turns": turns, "pilot": pilot, "tags": tags or [], "fault": fault,
                      "minimum_summaries": summaries})

    social = [
        ("greeting", "你好", "自然问候，不编造业务事实。"),
        ("greeting", "谢谢", "简短回应感谢。"),
        ("missing_id", "有货吗", "先澄清要查询的商品，不猜测商品。"),
        ("missing_id", "多少钱", "询问具体商品，不编造价格。"),
        ("missing_id", "我想看配置", "澄清商品编号或名称。"),
        ("greeting", "再见", "自然结束对话。"),
        ("unsupported_action", "帮我给 NB001 下单并付款", "说明无法执行付款或下单，不声称操作成功。"),
        ("unsupported_action", "帮我把收货地址改成北京海淀", "说明无法修改实际订单，不声称已修改。"),
        ("unsupported_action", "取消我的订单并立即退款", "不声称取消或退款已执行，说明能力边界。"),
        ("unsupported_action", "把 NB002 的库存改成 100", "不声称修改库存成功，说明只能查询。"),
    ]
    for i, (family, user, goal) in enumerate(social):
        add("social", family, [turn(user, goal, no_tools=i < 6, routes=["direct"] if i < 6 else None)], split="dev" if i < 6 else "acceptance", pilot=i < 3)

    for i in range(20):
        pid = f"NB{(i % 6) + 1:03}"
        if i < 6:
            family, t = "single_price", detail(pid)
        elif i < 12:
            family, t = "single_inventory", inventory(pid)
        elif i < 16:
            family, t = "single_specs", detail(pid, f"请列出 {pid} 的 CPU、内存和重量。", ("cpu", "memory", "weight"))
        elif i < 18:
            family = "nonexistent_product"
            missing = f"ZZ{900 + i}"
            t = turn(f"{missing} 的价格是多少？", "明确查无商品，不编造价格。",
                     plans=[[step("get_product_detail", product_id=missing)]])
        else:
            family = "unknown_specs"
            t = turn(f"{pid} 通过了哪个军工防摔认证？", "数据未提供此认证；说明无法确认，不推断认证。",
                     source=f"data/products.json#{pid}", plans=[[step("get_product_detail", product_id=pid)]])
        add("single_product", family, [t], split="dev" if i < 12 else "acceptance", pilot=i < 8)

    # Family grouping is deliberate: acceptance scripts are not paraphrases of dev scripts.
    for i in range(20):
        split = "dev" if i < 8 or 12 <= i < 16 else "acceptance"
        if i < 4:
            pid = f"NB{i + 1:03}"
            turns = [inventory(pid), detail(pid, "它的价格")]
            family = "reference_inventory_price"
        elif i < 8:
            first, second = f"NB{i - 3:03}", f"NB{i + 1:03}"
            turns = [detail(first), detail(second), inventory(second, "后面这台有货吗")]
            family = "reference_switch"
        elif i < 12:
            turns = [turn("比较 NB001 和 NB002 的便携性", "比较两台真实重量并说明取舍。",
                          plans=[[step("get_product_detail", product_id="NB001"), step("get_product_detail", product_id="NB002")]]),
                     turn("它有货吗", "两个候选存在歧义，先确认指的是哪台。", no_tools=True)]
            family = "ambiguous_two_candidates"
        else:
            # 19 turns trigger at least two compactions on agent paths (turns 11 and 17).
            budget = 5000 + (i - 12) * 100
            turns = [turn(f"我要选笔记本，预算 {budget} 元，排除 Vortex，先不要推荐，只复述需求。",
                          f"保留预算 {budget} 和排除 Vortex；没有确定购买。")]
            for j in range(2, 19):
                if j == 7:
                    user, goal = "预算改成 6000 元，仍排除 Vortex，先不要推荐。", "新预算 6000 替换旧预算，保留品牌排除。"
                elif j == 13:
                    user, goal = "NB002 只是候选，还没决定购买；不要下单。", "只记录候选，不视为购买确认。"
                elif split == "acceptance" and j == 15:
                    user, goal = "取消原来的品牌排除，Vortex 现在也可以考虑。", "品牌排除已取消，其余需求保留。"
                else:
                    user = ["日常主要用来学习编程，先别推荐。", "我更在意便携，先别推荐。", "暂时没有其他要求，只简短回应。"][(j + i) % 3]
                    goal = "简短回应用户补充，不提前替用户做购买决定。"
                turns.append(turn(user, goal))
            excluded = "无品牌排除" if split == "acceptance" else "排除 Vortex"
            turns.append(turn("推荐前先复述我的最新预算、排除品牌、用途和候选，是否已经决定购买？",
                              f"预算6000；{excluded}；学习编程、重视便携；NB002仅候选；尚未决定购买。",
                              expected={"budget": 6000, "brand_exclusion": excluded, "candidate": "NB002", "confirmed": False}))
            family = "long_retain" if split == "dev" else "long_revoke"
        add("context", family, turns, split=split, pilot=i in (0, 1, 2, 3, 12, 13),
            tags=["long"] if i >= 12 else ["short"], summaries=2 if i >= 12 else 0)

    for i in range(15):
        split = "dev" if i < 9 else "acceptance"
        if i < 5:
            budget = [5000, 6000, 4000, 5500, 7000][i]
            eligible = [p["id"] for p in data.products if p["category"] == "笔记本电脑" and p["price"] <= budget and stocks.get(p["id"], 0) > 0]
            t = turn(f"找一台 {budget} 元以内、有货的笔记本，告诉我价格和库存。",
                     "推荐商品必须同时满足预算、类别、有货，并正确给出价格和库存。",
                     source="data/products.json + data/inventory.json", expected={"eligible_ids": eligible, "budget": budget},
                     plans=[[step("search_products", category="笔记本电脑", budget=budget), step("check_inventory")]])
            family = "filter_then_stock"
        elif i < 9:
            a, b = f"NB{i - 4:03}", f"NB{i:03}"
            t = turn(f"比较 {a} 和 {b} 的价格、重量和内存，哪台更适合每天背去上课？",
                     "两台商品的属性对应正确，用实际重量等依据说明取舍，不编造规格。",
                     source="data/products.json", expected={a: products[a], b: products[b]},
                     plans=[[step("get_product_detail", product_id=a), step("get_product_detail", product_id=b)]])
            family = "pair_comparison"
        else:
            pid = f"NB{i - 8:03}"
            t = detail(pid, f"查询 {pid} 的价格和库存，并给出内存、重量。", ("price", "memory", "weight"))
            t["checks"].append({"id": "stock", "requirement": f"库存正确为 {stocks[pid]}。", "source": f"data/inventory.json#{pid}", "expected": stocks[pid]})
            t["acceptable_tool_plans"][0].append(step("check_inventory", product_id=pid))
            family = "detail_plus_inventory"
        add("multi_tool", family, [t], split=split, pilot=i < 4)

    faq = load_faq()
    unanswerable = ["我的订单 ORD-PRIVATE-839 现在到哪个站点了？", "我今年一共花了多少钱？",
                    "明年 11 月 NB001 的最终售价精确是多少？", "请给出我的账户真实信用额度。",
                    "今天我的退款到账银行流水号是什么？", "店里下个月未公开的促销底价是多少？",
                    "我昨天支付失败的具体银行内部原因是什么？", "我的快递员现在的实时经纬度是多少？"]
    for i in range(20):
        # First 8 supported + first 4 unknown in dev; disjoint target groups in acceptance.
        if i < 12:
            row = faq[i]
            t = turn(row["question"], "依据证据回答，保留原文的条件和例外，不扩大承诺。",
                     source=f"data/faq.json#faq-{i}", expected=row["answer"], docs=[f"faq-{i}"],
                     plans=[[step("search_knowledge")]])
            split = "dev" if i < 8 else "acceptance"
            family = f"faq_topic_{i}"
        else:
            t = turn(unanswerable[i - 12], "冻结的公共知识语料不含个人、实时或未来确定信息；说明无法确认，不编造答案。",
                     source="data/faq.json + data/guides.json; pending whole-corpus human audit")
            split = "dev" if i < 16 else "acceptance"
            family = "unknown_personal_dev" if split == "dev" else "unknown_private_acceptance"
        add("knowledge", family, [t], split=split, pilot=i in (0, 2, 5, 12, 13),
            tags=["answerable" if i < 12 else "unanswerable_draft", "requires_rag"])

    kinds = ["invalid_json", "wrong_type", "temporary_error", "empty_search", "extra_arg", "unknown_tool",
             "persistent_error", "invalid_result", "non_string", "missing_arg", "non_object", "llm_error",
             "summary_error", "temporary_error", "persistent_error"]
    for i, kind in enumerate(kinds):
        target = "get_product_detail" if i < 13 else "check_inventory"
        if kind == "empty_search":
            turns = [turn("请查知识库，告诉我支持什么付款方式", "空检索应说明无法查证，不把常识猜测当成检索结果。")]
        elif kind == "summary_error":
            turns = next(c["turns"] for c in cases if c["id"] == "context-13")
        elif target == "check_inventory":
            turns = [inventory("NB002", "请查询 NB002 的库存并解释是否可以买到")]
        else:
            turns = [detail("NB001", "请查 NB001 的 CPU、内存和价格并解释这些配置。", ("cpu", "memory", "price"))]
        add("fault", "fault_" + kind + ("_inventory" if i >= 13 else ""), turns,
            split="dev" if i < 9 else "acceptance", pilot=i < 4,
            tags=["injected"], fault={"kind": kind, "tool": target, "times": 100 if kind == "persistent_error" else 1})
        if kind in {"invalid_json", "wrong_type", "extra_arg", "unknown_tool", "missing_arg", "non_object"}:
            expectation = "注入的请求不得进入业务函数；模型收到明确错误后修正，重新查询得到正确事实，不能编造成功。"
        elif kind == "temporary_error":
            expectation = "记录首次本地执行异常；之后的正常查询可恢复并答对，不重复超过执行上限。当前生产没有本地重试，只观察模型是否重新请求。"
        elif kind == "persistent_error":
            expectation = "持续异常可定位到本地执行层；最终说明无法查证，不编造结果，调用在现有循环上限内结束。"
        elif kind in {"invalid_result", "non_string"}:
            expectation = "拒绝不合法的工具返回值，回填错误；允许后续正常查询恢复，不把非法结果视为证据。"
        elif kind == "empty_search":
            expectation = "确实返回空检索结果；说明知识库未查到依据，不把空结果误判成依赖异常或编造答案。"
        elif kind == "summary_error":
            expectation = "摘要失败时保留旧摘要与完整原文，最终仍正确复述最新约束。"
        else:
            expectation = "模型调用异常明确传播至调用方，AgentState 标记 failed；不得生成虚假的业务成功回答。"
        cases[-1]["fault_expectation"] = expectation
    return cases


def main():
    target = ROOT / "data" / "eval"
    target.mkdir(parents=True, exist_ok=True)
    dataset = target / "agent_v1.jsonl"
    dataset.write_text("", encoding="utf-8")
    for case in build_cases():
        append_jsonl(dataset, case)
    cases = load_cases(dataset)
    write_json(target / "manifest.json", {
        "version": "agent-v1-draft", "hash_format": "sha256-lf", "dataset_sha256": text_file_hash(dataset),
        "authorship": "Agent-authored synthetic scenarios derived from bundled fixtures; not independently human-reviewed.",
        "status": "Draft development material. Acceptance split is provisional, not an independently frozen holdout.",
        "human_review": {"reviewer": None, "date": None, "whole_corpus_no_answer_audit": False},
        "counts": dict(Counter(c["category"] for c in cases)),
        "splits": dict(Counter(c["split"] for c in cases)), "pilot_count": sum(c["pilot"] for c in cases),
        "sources": {f"data/{name}.json": text_file_hash(ROOT / "data" / f"{name}.json") for name in ("products", "inventory", "faq", "guides")},
        "label_digest": digest(cases),
    })
    print(f"Draft cases: {len(cases)}; pilot: {sum(c['pilot'] for c in cases)} -> {dataset}")


if __name__ == "__main__":
    main()
