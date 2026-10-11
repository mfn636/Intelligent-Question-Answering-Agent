"""默认装配本地商品工具，不创建或依赖 RAG 服务。"""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bootstrap
import main
from contract.data import EcommerceData
from contract.retrieval import SearchHit
from storage.json_loader import load_ecommerce_data
from tool_definitions.tool_tables import build_tool_tables
from tool_providers.local import LocalProvider


LOCAL_TOOLS = {"search_products", "get_product_detail", "check_inventory"}
DEFAULT_TOOLS = LOCAL_TOOLS | {"search_knowledge"}


def tool_names(schemas):
    return {schema["function"]["name"] for schema in schemas}


def tool_call(name, args):
    return SimpleNamespace(
        id=name, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class LocalToolBootstrapTests(unittest.TestCase):
    def test_default_application_exposes_real_product_and_inventory_data(self):
        with bootstrap.create_application(llm=Mock()) as app:
            self.assertIsInstance(app.tools, LocalProvider)
            self.assertEqual(tool_names(app.tools.list_tools()), DEFAULT_TOOLS)
            products = json.loads(app.tools.call("search_products", {}))
            self.assertTrue(products)
            product = products[0]
            detail = json.loads(app.tools.call("get_product_detail", {"product_id": product["id"]}))
            self.assertEqual(detail, [product])
            inventory = json.loads(app.tools.call("check_inventory", {"product_id": product["id"]}))
            self.assertTrue(inventory)
            self.assertEqual(inventory[0]["product_name"], product["name"])
            search_service = app.tools._tool_lookup["search_knowledge"]["fn"].keywords["search_service"]
            self.assertIsNone(search_service._service)
            with self.assertRaises(KeyError):
                app.tools.call("search_faq", {"query": "退款"})

    def test_one_data_load_per_application_and_shared_provider_across_sessions(self):
        with patch("bootstrap.load_ecommerce_data", wraps=load_ecommerce_data) as loader:
            with bootstrap.create_application(llm=Mock()) as app:
                first = app.create_conversation("first")
                second = app.create_conversation("second")
                loader.assert_called_once_with()
                self.assertIs(first.executor.tools, app.tools)
                self.assertIs(second.executor.tools, app.tools)
                self.assertIsNot(first.history, second.history)
                self.assertIsNot(first.state, second.state)

    def test_default_knowledge_tool_uses_lazy_search_and_closes_with_application(self):
        with bootstrap.create_application(llm=Mock()) as app:
            search_service = app.tools._tool_lookup["search_knowledge"]["fn"].keywords["search_service"]
            fake_service = Mock()
            fake_service.search.return_value = [SearchHit(
                id="faq-1", text="退款规则正文", metadata={"source": "faq", "question": "如何退款"}, score=0.9,
            )]
            search_service._factory = lambda: fake_service
            result = json.loads(app.tools.call("search_knowledge", {"query": "如何退款"}))
            self.assertEqual(result[0]["content"], "退款规则正文")
            self.assertIs(search_service._service, fake_service)
        with self.assertRaises(RuntimeError):
            search_service.search("knowledge", "退款")

    def test_injected_provider_is_used_without_loading_default_data(self):
        provider = LocalProvider(tool_schemas=[], tool_lookup={})
        llm = Mock()
        with patch("bootstrap.load_ecommerce_data", side_effect=AssertionError("unexpected load")):
            with bootstrap.create_application(llm=llm, tools=provider) as app:
                self.assertIs(app.tools, provider)
                self.assertEqual(app.create_conversation().executor.tools.list_tools(), [])
        llm.close.assert_not_called()

    def test_initialization_failure_closes_owned_llm(self):
        closed = Mock()

        def build_llm(resources):
            resources.callback(closed)
            return Mock()

        with (
            patch("bootstrap._build_llm", side_effect=build_llm),
            patch("bootstrap.load_ecommerce_data", side_effect=ValueError("invalid data")),
        ):
            with self.assertRaisesRegex(ValueError, "invalid data"):
                bootstrap.create_application()
        closed.assert_called_once_with()

    def test_default_cli_passes_three_schemas_executes_tools_and_returns_results(self):
        llm = Mock(usage_log=[])
        requests = [
            tool_call("search_products", {"budget": "5000"}),
            tool_call("search_products", {"budget": 5000}),
            tool_call("get_product_detail", {"product_id": "NB001"}),
            tool_call("check_inventory", {"product_id": "NB001"}),
        ]
        requests[0].id = "invalid-search"
        seen_results = []

        def chat(*, messages, tools):
            self.assertEqual(tool_names(tools), DEFAULT_TOOLS)
            tool_messages = [message for message in messages if message["role"] == "tool"]
            if not tool_messages:
                return SimpleNamespace(content=None, tool_calls=requests)
            seen_results.extend(json.loads(message["content"]) for message in tool_messages)
            return SimpleNamespace(content="商品和库存查询完成", tool_calls=[])

        llm.chat.side_effect = chat
        closed = Mock()

        def build_llm(resources):
            resources.callback(closed)
            return llm

        with (
            patch("bootstrap._build_llm", side_effect=build_llm),
            patch("builtins.input", side_effect=["查商品和库存", "exit"]),
            patch("builtins.print") as output,
        ):
            main.main()
        self.assertIn("budget", seen_results[0]["error"])
        self.assertTrue(seen_results[1])
        self.assertTrue(all(product["price"] <= 5000 for product in seen_results[1]))
        self.assertEqual(seen_results[2][0]["id"], "NB001")
        self.assertEqual(seen_results[3][0]["product_id"], "NB001")
        output.assert_any_call("AI：", "商品和库存查询完成")
        closed.assert_called_once_with()

    def test_table_builder_adds_knowledge_only_for_explicit_search_service(self):
        data = EcommerceData()
        for service in (None, Mock()):
            with self.subTest(service=service):
                schemas, lookup = build_tool_tables(data=data, search_service=service)
                expected = LOCAL_TOOLS if service is None else LOCAL_TOOLS | {"search_knowledge"}
                self.assertEqual(tool_names(schemas), expected)
                self.assertEqual(set(lookup), expected)
                if service is not None:
                    service.search.return_value = []
                    provider = LocalProvider(tool_schemas=schemas, tool_lookup=lookup)
                    self.assertEqual(provider.call("search_knowledge", {"query": "退款"}), "[]")
                    service.search.assert_called_once_with("knowledge", "退款", top_k=5, filters=None)

    def test_import_and_default_startup_do_not_require_rag_or_faq(self):
        # 新进程中阻断 RAG 的重量级依赖，确保默认启动只注册延迟服务。
        script = '''
import builtins
import os
import sys
from unittest.mock import Mock, patch
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in ("qdrant_client", "rag.store", "rag.retriever", "rag.embedder",
                "tool_functions.faq_search"):
        raise AssertionError("Unexpected dependency: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from pathlib import Path
root = Path.cwd()
original_open = builtins.open
original_path_open = Path.open
def check_read(filename):
    if isinstance(filename, (str, os.PathLike)):
        path = Path(filename).resolve()
        if path.is_relative_to(root / "data") or path == root / ".env":
            raise AssertionError("Business data/config read on import: " + str(path))
def guarded_open(filename, *args, **kwargs):
    check_read(filename)
    return original_open(filename, *args, **kwargs)
def guarded_path_open(path, *args, **kwargs):
    check_read(path)
    return original_path_open(path, *args, **kwargs)
with patch("builtins.open", guarded_open), patch.object(
    Path, "open", guarded_path_open
):
    import bootstrap
with patch("storage.json_loader.load_faq", side_effect=AssertionError("FAQ loaded")), patch(
    "storage.json_loader.load_guides", side_effect=AssertionError("guides loaded")
):
    with bootstrap.create_application(llm=Mock()) as app:
        assert len(app.tools.list_tools()) == 4
        assert app.create_conversation().executor.tools is app.tools
assert "qdrant_client" not in sys.modules
assert "rag.store" not in sys.modules
assert "rag.retriever" not in sys.modules
assert "rag.embedder" not in sys.modules
'''
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
