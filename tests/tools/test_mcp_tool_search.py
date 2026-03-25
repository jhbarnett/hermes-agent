"""Tests for the deferred MCP tool loading (ToolSearch pattern).

All tests use mocks -- no real MCP servers or subprocesses are started.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mcp_tool(name="read_file", description="Read a file", input_schema=None):
    """Create a fake MCP Tool object matching the SDK interface."""
    tool = SimpleNamespace()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
        },
        "required": ["path"],
    }
    return tool


def _make_mock_server(name, session=None, tools=None):
    """Create an MCPServerTask with mock attributes for testing."""
    from tools.mcp_tool import MCPServerTask
    server = MCPServerTask(name)
    server.session = session
    server._tools = tools or []
    return server


def _make_schema(name, description="A test tool"):
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": {}},
    }


def _dummy_handler(args, **kwargs):
    return json.dumps({"ok": True})


def _reset_deferred_state():
    """Reset module-level deferred tool state between tests."""
    import tools.mcp_tool as mod
    mod._deferred_tools.clear()
    mod._session_activated.clear()
    mod._tools_dirty = False


# ---------------------------------------------------------------------------
# _should_defer_tools
# ---------------------------------------------------------------------------

class TestShouldDeferTools:
    """Tests for the threshold-based deferral decision."""

    def test_auto_defers_above_threshold(self):
        from tools.mcp_tool import _should_defer_tools
        config = {"mcp_tool_search": {"enabled": "auto", "threshold": 10}}
        with patch("hermes_cli.config.load_config", return_value=config):
            assert _should_defer_tools(11) is True

    def test_auto_does_not_defer_at_threshold(self):
        from tools.mcp_tool import _should_defer_tools
        config = {"mcp_tool_search": {"enabled": "auto", "threshold": 10}}
        with patch("hermes_cli.config.load_config", return_value=config):
            assert _should_defer_tools(10) is False

    def test_auto_does_not_defer_below_threshold(self):
        from tools.mcp_tool import _should_defer_tools
        config = {"mcp_tool_search": {"enabled": "auto", "threshold": 10}}
        with patch("hermes_cli.config.load_config", return_value=config):
            assert _should_defer_tools(5) is False

    def test_enabled_true_always_defers(self):
        from tools.mcp_tool import _should_defer_tools
        config = {"mcp_tool_search": {"enabled": True}}
        with patch("hermes_cli.config.load_config", return_value=config):
            assert _should_defer_tools(1) is True

    def test_enabled_false_never_defers(self):
        from tools.mcp_tool import _should_defer_tools
        config = {"mcp_tool_search": {"enabled": False}}
        with patch("hermes_cli.config.load_config", return_value=config):
            assert _should_defer_tools(1000) is False

    def test_no_config_uses_defaults(self):
        """Missing config falls back to auto with default threshold."""
        from tools.mcp_tool import _should_defer_tools, _DEFAULT_TOOL_SEARCH_THRESHOLD
        with patch("hermes_cli.config.load_config", return_value={}):
            assert _should_defer_tools(_DEFAULT_TOOL_SEARCH_THRESHOLD) is False
            assert _should_defer_tools(_DEFAULT_TOOL_SEARCH_THRESHOLD + 1) is True


# ---------------------------------------------------------------------------
# _score_term
# ---------------------------------------------------------------------------

class TestScoreTerm:
    """Tests for the word-boundary scoring function."""

    def test_exact_word_returns_3(self):
        from tools.mcp_tool import _score_term
        assert _score_term("list", ["mcp", "stripe", "list", "customers"], "mcp_stripe_list_customers") == 3

    def test_prefix_returns_2(self):
        from tools.mcp_tool import _score_term
        assert _score_term("cust", ["mcp", "stripe", "list", "customers"], "mcp_stripe_list_customers") == 2

    def test_substring_returns_1(self):
        from tools.mcp_tool import _score_term
        # "stri" is substring of the text but not a word prefix (no word starts with "stri"... actually "stripe" does)
        # Use a clearer case: "trip" is in "stripe" but no word starts with "trip"
        assert _score_term("trip", ["mcp", "stripe", "list"], "mcp_stripe_list") == 1

    def test_no_match_returns_0(self):
        from tools.mcp_tool import _score_term
        assert _score_term("zzz", ["mcp", "stripe", "list"], "mcp_stripe_list") == 0

    def test_short_term_skips_prefix(self):
        """Terms < 3 chars don't get prefix score, only exact or substring."""
        from tools.mcp_tool import _score_term
        # "go" is prefix of "google" but len("go") < 3, so no prefix match
        # "go" IS a substring of "google" though
        assert _score_term("go", ["google", "search"], "google_search") == 1

    def test_short_term_exact_still_works(self):
        """Short terms still match exact words."""
        from tools.mcp_tool import _score_term
        assert _score_term("go", ["go", "live"], "go_live") == 3

    def test_exact_wins_over_prefix(self):
        """When term is both an exact word and prefix of another, exact wins."""
        from tools.mcp_tool import _score_term
        # "list" is exact word AND prefix of "listing" — should return 3 (exact)
        assert _score_term("list", ["list", "listing"], "list_listing") == 3


# ---------------------------------------------------------------------------
# _search_deferred_tools
# ---------------------------------------------------------------------------

class TestSearchDeferredTools:
    """Tests for keyword search over deferred tools."""

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def _populate_deferred(self, tools):
        """Load tools into the deferred storage."""
        import tools.mcp_tool as mod
        for name, desc in tools:
            mod._deferred_tools[name] = {
                "schema": _make_schema(name, desc),
                "handler": _dummy_handler,
                "check_fn": None,
                "toolset": "mcp-test",
                "description": desc,
            }

    def test_matches_by_name(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List all customers"),
            ("mcp_github_list_repos", "List repositories"),
        ])
        results = _search_deferred_tools("stripe")
        assert len(results) == 1
        assert results[0]["name"] == "mcp_stripe_list_customers"

    def test_matches_by_description(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List all customers"),
            ("mcp_posthog_query_trends", "Query analytics trends"),
        ])
        results = _search_deferred_tools("analytics")
        assert len(results) == 1
        assert results[0]["name"] == "mcp_posthog_query_trends"

    def test_name_matches_scored_higher_than_description(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_create_payment", "Create a new payment"),
            ("mcp_github_list_repos", "List repos with payment info"),
        ])
        results = _search_deferred_tools("payment", max_results=2)
        # stripe tool matches in both name and description, github only description
        assert results[0]["name"] == "mcp_stripe_create_payment"

    def test_multi_term_query(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List all customers"),
            ("mcp_stripe_create_payment", "Create a new payment link"),
            ("mcp_github_list_repos", "List repositories"),
        ])
        results = _search_deferred_tools("stripe payment")
        # "stripe payment" should rank create_payment highest (matches both terms in name)
        assert results[0]["name"] == "mcp_stripe_create_payment"

    def test_max_results_respected(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            (f"mcp_test_tool_{i}", "A test tool") for i in range(20)
        ])
        results = _search_deferred_tools("test", max_results=3)
        assert len(results) == 3

    def test_no_matches_returns_empty(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List all customers"),
        ])
        results = _search_deferred_tools("nonexistent_xyz")
        assert results == []

    def test_empty_query_returns_empty(self):
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List all customers"),
        ])
        results = _search_deferred_tools("")
        assert results == []

    def test_exact_word_beats_prefix(self):
        """Exact word boundary match in name scores higher than prefix match."""
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List customers"),           # "list" is exact word
            ("mcp_stripe_list_payments", "Payments listing endpoint"), # "list" is exact word too
            ("mcp_github_listeners_get", "Get event listeners"),       # "list" is prefix of "listeners"
        ])
        results = _search_deferred_tools("list", max_results=3)
        # Both stripe tools have "list" as exact word — should rank above github
        result_names = [r["name"] for r in results]
        assert result_names[-1] == "mcp_github_listeners_get"

    def test_exact_word_beats_substring(self):
        """Exact word match ranks above mere substring containment."""
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_github_read_resource", "Read a resource"),      # "read" exact word
            ("mcp_posthog_already_done", "A preread cache check"), # "read" substring of "preread"
        ])
        results = _search_deferred_tools("read", max_results=2)
        assert results[0]["name"] == "mcp_github_read_resource"

    def test_short_terms_skip_prefix_matching(self):
        """Terms shorter than 3 chars should not trigger prefix matching."""
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_go_live", "Go live with Stripe"),  # "go" is exact word
            ("mcp_github_got_repos", "Got repositories"),   # "go" is prefix of "got" but len < 3
        ])
        results = _search_deferred_tools("go", max_results=2)
        # "go" exact word match in stripe tool, no prefix boost for "got"
        assert results[0]["name"] == "mcp_stripe_go_live"

    def test_server_name_disambiguates_generic_action(self):
        """Adding server name to query disambiguates generic verbs like 'list'."""
        from tools.mcp_tool import _search_deferred_tools
        self._populate_deferred([
            ("mcp_stripe_list_customers", "List all Stripe customers"),
            ("mcp_github_list_repos", "List GitHub repositories"),
            ("mcp_posthog_list_events", "List PostHog events"),
        ])
        results = _search_deferred_tools("stripe list", max_results=2)
        assert results[0]["name"] == "mcp_stripe_list_customers"

    def test_empty_deferred_returns_empty(self):
        from tools.mcp_tool import _search_deferred_tools
        results = _search_deferred_tools("stripe")
        assert results == []


# ---------------------------------------------------------------------------
# _activate_tools
# ---------------------------------------------------------------------------

class TestActivateTools:
    """Tests for moving deferred tools to the live registry."""

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def _populate_deferred(self, names):
        import tools.mcp_tool as mod
        for name in names:
            mod._deferred_tools[name] = {
                "schema": _make_schema(name),
                "handler": _dummy_handler,
                "check_fn": None,
                "toolset": "mcp-test",
                "description": f"Tool {name}",
            }

    def test_activates_into_registry(self):
        from tools.mcp_tool import _activate_tools

        mock_registry = ToolRegistry()
        self._populate_deferred(["mcp_test_alpha", "mcp_test_beta"])

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            activated = _activate_tools(["mcp_test_alpha"], session_id="s1")

        assert activated == ["mcp_test_alpha"]
        assert "mcp_test_alpha" in mock_registry.get_all_tool_names()
        assert "mcp_test_beta" not in mock_registry.get_all_tool_names()

    def test_sets_tools_dirty_flag(self):
        from tools.mcp_tool import _activate_tools, check_tools_dirty

        mock_registry = ToolRegistry()
        self._populate_deferred(["mcp_test_alpha"])

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_test_alpha"], session_id="s1")

        assert check_tools_dirty() is True
        # Second call should return False (flag cleared)
        assert check_tools_dirty() is False

    def test_idempotent_within_session(self):
        """Activating the same tool twice in the same session doesn't re-register."""
        from tools.mcp_tool import _activate_tools
        import tools.mcp_tool as mod

        mock_registry = ToolRegistry()
        self._populate_deferred(["mcp_test_alpha"])

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            first = _activate_tools(["mcp_test_alpha"], session_id="s1")
            mod._tools_dirty = False
            second = _activate_tools(["mcp_test_alpha"], session_id="s1")

        assert first == ["mcp_test_alpha"]
        assert second == []  # already activated in this session
        assert mod._tools_dirty is False

    def test_unknown_tool_name_ignored(self):
        from tools.mcp_tool import _activate_tools

        mock_registry = ToolRegistry()
        self._populate_deferred(["mcp_test_alpha"])

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            activated = _activate_tools(["nonexistent_tool"], session_id="s1")

        assert activated == []

    def test_activated_tool_is_dispatchable(self):
        """An activated tool's handler should work via registry.dispatch."""
        from tools.mcp_tool import _activate_tools

        mock_registry = ToolRegistry()
        self._populate_deferred(["mcp_test_alpha"])

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_test_alpha"], session_id="s1")

        result = json.loads(mock_registry.dispatch("mcp_test_alpha", {}))
        assert result == {"ok": True}

    def test_different_sessions_share_registry(self):
        """Two sessions activating the same tool: only one registry.register call."""
        from tools.mcp_tool import _activate_tools

        mock_registry = ToolRegistry()
        self._populate_deferred(["mcp_test_alpha"])

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_test_alpha"], session_id="s1")
            _activate_tools(["mcp_test_alpha"], session_id="s2")

        # Tool registered once, referenced by two sessions
        assert mock_registry.get_all_tool_names() == ["mcp_test_alpha"]


# ---------------------------------------------------------------------------
# _defer_all_mcp_tools
# ---------------------------------------------------------------------------

class TestDeferAllMcpTools:
    """Tests for the bulk deferral of registered MCP tools."""

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def test_moves_tools_from_registry_to_deferred(self):
        from tools.mcp_tool import _defer_all_mcp_tools
        import tools.mcp_tool as mod

        mock_registry = ToolRegistry()
        # Register some MCP tools
        for name in ["mcp_github_list_repos", "mcp_github_create_pr"]:
            mock_registry.register(
                name=name, toolset="mcp-github",
                schema=_make_schema(name), handler=_dummy_handler,
            )

        # Set up _servers so _existing_tool_names works
        server = _make_mock_server("github", session=SimpleNamespace())
        server._registered_tool_names = ["mcp_github_list_repos", "mcp_github_create_pr"]

        toolsets = {
            "mcp-github": {
                "description": "MCP tools from github server",
                "tools": ["mcp_github_list_repos", "mcp_github_create_pr"],
                "includes": [],
            },
            "github": {
                "description": "MCP server 'github' tools",
                "tools": ["mcp_github_list_repos", "mcp_github_create_pr"],
                "includes": [],
            },
            "hermes-cli": {
                "description": "Core CLI tools",
                "tools": ["bash", "mcp_github_list_repos", "mcp_github_create_pr"],
                "includes": [],
            },
        }

        old_servers = mod._servers.copy()
        mod._servers["github"] = server
        try:
            with patch("tools.registry.registry", mock_registry), \
                 patch("toolsets.TOOLSETS", toolsets):
                count = _defer_all_mcp_tools()
        finally:
            mod._servers.clear()
            mod._servers.update(old_servers)

        # Tools should be removed from registry
        assert count == 2
        assert mock_registry.get_all_tool_names() == []

        # Tools should be in deferred storage
        assert "mcp_github_list_repos" in mod._deferred_tools
        assert "mcp_github_create_pr" in mod._deferred_tools

        # MCP toolsets should be cleaned up
        assert "mcp-github" not in toolsets
        assert "github" not in toolsets

        # MCP tools should be removed from hermes-* umbrella
        assert "mcp_github_list_repos" not in toolsets["hermes-cli"]["tools"]
        assert "bash" in toolsets["hermes-cli"]["tools"]

    def test_preserves_handler_and_schema(self):
        """Deferred tools retain their original schema and handler."""
        from tools.mcp_tool import _defer_all_mcp_tools
        import tools.mcp_tool as mod

        schema = _make_schema("mcp_test_tool", "My test tool")
        mock_registry = ToolRegistry()
        mock_registry.register(
            name="mcp_test_tool", toolset="mcp-test",
            schema=schema, handler=_dummy_handler,
        )

        server = _make_mock_server("test", session=SimpleNamespace())
        server._registered_tool_names = ["mcp_test_tool"]

        old_servers = mod._servers.copy()
        mod._servers["test"] = server
        try:
            with patch("tools.registry.registry", mock_registry), \
                 patch("toolsets.TOOLSETS", {
                     "mcp-test": {"description": "MCP tools from test server", "tools": ["mcp_test_tool"], "includes": []},
                 }):
                _defer_all_mcp_tools()
        finally:
            mod._servers.clear()
            mod._servers.update(old_servers)

        deferred = mod._deferred_tools["mcp_test_tool"]
        assert deferred["schema"]["name"] == "mcp_test_tool"
        assert deferred["schema"]["description"] == "My test tool"
        assert deferred["handler"] is _dummy_handler


# ---------------------------------------------------------------------------
# search_mcp_tools handler (end-to-end)
# ---------------------------------------------------------------------------

class TestSearchMcpToolsHandler:
    """Tests for the search_mcp_tools tool handler."""

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def _setup_and_register(self):
        """Populate deferred tools and register the search tool."""
        import tools.mcp_tool as mod

        tools_data = [
            ("mcp_stripe_list_customers", "List all Stripe customers"),
            ("mcp_stripe_create_payment", "Create a payment link in Stripe"),
            ("mcp_github_list_repos", "List GitHub repositories"),
            ("mcp_github_create_pr", "Create a pull request on GitHub"),
            ("mcp_posthog_query_trends", "Query PostHog analytics trends"),
        ]
        for name, desc in tools_data:
            mod._deferred_tools[name] = {
                "schema": _make_schema(name, desc),
                "handler": _dummy_handler,
                "check_fn": None,
                "toolset": f"mcp-{name.split('_')[1]}",
                "description": desc,
            }

        mock_registry = ToolRegistry()
        mock_toolsets = {}
        return mock_registry, mock_toolsets

    def test_search_returns_matching_tools(self):
        from tools.mcp_tool import _register_search_tool
        mock_registry, mock_toolsets = self._setup_and_register()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets):
            _register_search_tool()

        # Call the handler
        result_json = mock_registry.dispatch("search_mcp_tools", {"query": "stripe"})
        result = json.loads(result_json)

        assert len(result["tools"]) == 2
        names = {t["name"] for t in result["tools"]}
        assert names == {"mcp_stripe_list_customers", "mcp_stripe_create_payment"}

    def test_search_activates_found_tools(self):
        from tools.mcp_tool import _register_search_tool, check_tools_dirty, get_session_filter
        import tools.mcp_tool as mod

        mock_registry = ToolRegistry()
        mock_toolsets = {}

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets):
            self._setup_and_register()
            _register_search_tool()
            # Pass task_id so handler scopes activation to this session
            mock_registry.dispatch("search_mcp_tools", {"query": "github"}, task_id="test-session")

        session_tools = get_session_filter("test-session")
        assert "mcp_github_list_repos" in session_tools
        assert "mcp_github_create_pr" in session_tools
        assert check_tools_dirty() is True

    def test_search_no_matches(self):
        from tools.mcp_tool import _register_search_tool
        mock_registry, mock_toolsets = self._setup_and_register()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets):
            _register_search_tool()

        result_json = mock_registry.dispatch("search_mcp_tools", {"query": "nonexistent_xyz"})
        result = json.loads(result_json)

        assert result["tools"] == []
        assert "No tools matched" in result["note"]
        assert result["total_deferred"] == 5

    def test_search_returns_full_schemas(self):
        """The handler should return parameter schemas so the model knows what to call."""
        from tools.mcp_tool import _register_search_tool
        mock_registry, mock_toolsets = self._setup_and_register()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets):
            _register_search_tool()

        result_json = mock_registry.dispatch("search_mcp_tools", {"query": "posthog"})
        result = json.loads(result_json)

        assert len(result["tools"]) == 1
        tool_def = result["tools"][0]
        assert "name" in tool_def
        assert "description" in tool_def
        assert "parameters" in tool_def

    def test_search_respects_max_results(self):
        from tools.mcp_tool import _register_search_tool
        mock_registry, mock_toolsets = self._setup_and_register()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets):
            _register_search_tool()

        result_json = mock_registry.dispatch(
            "search_mcp_tools",
            {"query": "mcp", "max_results": 2},
        )
        result = json.loads(result_json)
        assert len(result["tools"]) == 2


# ---------------------------------------------------------------------------
# check_tools_dirty
# ---------------------------------------------------------------------------

class TestCheckToolsDirty:
    """Tests for the dirty-flag mechanism."""

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def test_initially_clean(self):
        from tools.mcp_tool import check_tools_dirty
        assert check_tools_dirty() is False

    def test_cleared_after_check(self):
        import tools.mcp_tool as mod
        from tools.mcp_tool import check_tools_dirty
        mod._tools_dirty = True
        assert check_tools_dirty() is True
        assert check_tools_dirty() is False

    def test_activation_sets_dirty(self):
        from tools.mcp_tool import _activate_tools, check_tools_dirty
        import tools.mcp_tool as mod

        mod._deferred_tools["mcp_test_tool"] = {
            "schema": _make_schema("mcp_test_tool"),
            "handler": _dummy_handler,
            "check_fn": None,
            "toolset": "mcp-test",
            "description": "test",
        }

        mock_registry = ToolRegistry()
        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_test_tool"], session_id="s1")

        assert check_tools_dirty() is True


# ---------------------------------------------------------------------------
# Session scoping (per-session activation + cleanup)
# ---------------------------------------------------------------------------

class TestSessionScoping:
    """Tests for per-session deferred tool activation."""

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def _populate_deferred(self, names):
        import tools.mcp_tool as mod
        for name in names:
            mod._deferred_tools[name] = {
                "schema": _make_schema(name),
                "handler": _dummy_handler,
                "check_fn": None,
                "toolset": "mcp-test",
                "description": f"Tool {name}",
            }

    def test_get_session_filter_returns_none_when_inactive(self):
        from tools.mcp_tool import get_session_filter
        assert get_session_filter("any") is None

    def test_get_session_filter_empty_for_new_session(self):
        from tools.mcp_tool import get_session_filter
        import tools.mcp_tool as mod
        mod._deferred_tools["x"] = {"schema": {}}  # activate deferred loading
        assert get_session_filter("new-session") == set()

    def test_get_session_filter_returns_session_tools(self):
        from tools.mcp_tool import _activate_tools, get_session_filter

        self._populate_deferred(["mcp_stripe_list", "mcp_github_list"])
        mock_registry = ToolRegistry()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_stripe_list"], session_id="s1")
            _activate_tools(["mcp_github_list"], session_id="s2")

        assert get_session_filter("s1") == {"mcp_stripe_list"}
        assert get_session_filter("s2") == {"mcp_github_list"}

    def test_parallel_sessions_isolated(self):
        """Two sessions activate different tools; each sees only its own."""
        from tools.mcp_tool import _activate_tools, get_session_filter

        self._populate_deferred(["mcp_stripe_list", "mcp_github_list", "mcp_posthog_query"])
        mock_registry = ToolRegistry()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_stripe_list"], session_id="thread-A")
            _activate_tools(["mcp_github_list"], session_id="thread-B")

        assert get_session_filter("thread-A") == {"mcp_stripe_list"}
        assert get_session_filter("thread-B") == {"mcp_github_list"}
        # Both in the global registry
        assert set(mock_registry.get_all_tool_names()) == {"mcp_stripe_list", "mcp_github_list"}

    def test_cleanup_session_removes_orphaned_tools(self):
        """Cleaning up a session unregisters tools no other session references."""
        from tools.mcp_tool import _activate_tools, cleanup_session

        self._populate_deferred(["mcp_stripe_list", "mcp_github_list"])
        mock_registry = ToolRegistry()
        mock_toolsets = {}

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_stripe_list"], session_id="s1")
            _activate_tools(["mcp_github_list"], session_id="s1")

            cleanup_session("s1")

        assert mock_registry.get_all_tool_names() == []

    def test_cleanup_preserves_shared_tools(self):
        """Tools referenced by another session survive cleanup."""
        from tools.mcp_tool import _activate_tools, cleanup_session

        self._populate_deferred(["mcp_stripe_list"])
        mock_registry = ToolRegistry()
        mock_toolsets = {}

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", mock_toolsets), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_stripe_list"], session_id="s1")
            _activate_tools(["mcp_stripe_list"], session_id="s2")

            cleanup_session("s1")

        # s2 still references it
        assert "mcp_stripe_list" in mock_registry.get_all_tool_names()

    def test_cleanup_nonexistent_session_is_noop(self):
        from tools.mcp_tool import cleanup_session
        cleanup_session("nonexistent")  # should not raise

    def test_deferred_tools_survive_cleanup(self):
        """Deferred storage is never cleared — tools can be re-searched."""
        from tools.mcp_tool import _activate_tools, cleanup_session
        import tools.mcp_tool as mod

        self._populate_deferred(["mcp_stripe_list"])
        mock_registry = ToolRegistry()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_stripe_list"], session_id="s1")
            cleanup_session("s1")

        assert "mcp_stripe_list" in mod._deferred_tools

    def test_subagent_inherits_parent_filter(self):
        """Subagents (no session_id) use _global bucket; parent filter unaffected."""
        from tools.mcp_tool import _activate_tools, get_session_filter

        self._populate_deferred(["mcp_stripe_list"])
        mock_registry = ToolRegistry()

        with patch("tools.registry.registry", mock_registry), \
             patch("toolsets.TOOLSETS", {}), \
             patch("toolsets.create_custom_toolset"):
            _activate_tools(["mcp_stripe_list"], session_id="parent-session")
            # Subagent activates without session_id (falls back to _global)
            _activate_tools(["mcp_stripe_list"])

        assert get_session_filter("parent-session") == {"mcp_stripe_list"}


# ---------------------------------------------------------------------------
# get_deferred_tool_count
# ---------------------------------------------------------------------------

class TestGetDeferredToolCount:

    def setup_method(self):
        _reset_deferred_state()

    def teardown_method(self):
        _reset_deferred_state()

    def test_counts_unactivated_tools(self):
        import tools.mcp_tool as mod
        from tools.mcp_tool import get_deferred_tool_count

        for i in range(5):
            mod._deferred_tools[f"tool_{i}"] = {"schema": {}, "handler": None}
        mod._session_activated["s1"] = {"tool_0", "tool_1"}

        assert get_deferred_tool_count() == 3

    def test_zero_when_empty(self):
        from tools.mcp_tool import get_deferred_tool_count
        assert get_deferred_tool_count() == 0


# ---------------------------------------------------------------------------
# Registry.unregister
# ---------------------------------------------------------------------------

class TestRegistryUnregister:
    """Tests for the new unregister method on ToolRegistry."""

    def test_unregister_removes_tool(self):
        reg = ToolRegistry()
        reg.register(
            name="alpha", toolset="core",
            schema=_make_schema("alpha"), handler=_dummy_handler,
        )
        entry = reg.unregister("alpha")
        assert entry is not None
        assert entry.name == "alpha"
        assert reg.get_all_tool_names() == []

    def test_unregister_returns_none_for_missing(self):
        reg = ToolRegistry()
        assert reg.unregister("nonexistent") is None

    def test_unregister_preserves_other_tools(self):
        reg = ToolRegistry()
        reg.register(
            name="alpha", toolset="core",
            schema=_make_schema("alpha"), handler=_dummy_handler,
        )
        reg.register(
            name="beta", toolset="core",
            schema=_make_schema("beta"), handler=_dummy_handler,
        )
        reg.unregister("alpha")
        assert reg.get_all_tool_names() == ["beta"]

    def test_unregistered_tool_not_dispatchable(self):
        reg = ToolRegistry()
        reg.register(
            name="alpha", toolset="core",
            schema=_make_schema("alpha"), handler=_dummy_handler,
        )
        reg.unregister("alpha")
        result = json.loads(reg.dispatch("alpha", {}))
        assert "error" in result


# ---------------------------------------------------------------------------
# _load_tool_search_config
# ---------------------------------------------------------------------------

class TestLoadToolSearchConfig:

    def test_defaults_when_no_config(self):
        from tools.mcp_tool import _load_tool_search_config, _DEFAULT_TOOL_SEARCH_THRESHOLD
        with patch("hermes_cli.config.load_config", return_value={}):
            cfg = _load_tool_search_config()
        assert cfg["enabled"] == "auto"
        assert cfg["threshold"] == _DEFAULT_TOOL_SEARCH_THRESHOLD

    def test_reads_explicit_values(self):
        from tools.mcp_tool import _load_tool_search_config
        config = {"mcp_tool_search": {"enabled": True, "threshold": 50}}
        with patch("hermes_cli.config.load_config", return_value=config):
            cfg = _load_tool_search_config()
        assert cfg["enabled"] is True
        assert cfg["threshold"] == 50

    def test_auto_string_normalized(self):
        from tools.mcp_tool import _load_tool_search_config
        config = {"mcp_tool_search": {"enabled": "Auto"}}
        with patch("hermes_cli.config.load_config", return_value=config):
            cfg = _load_tool_search_config()
        assert cfg["enabled"] == "auto"

    def test_disabled_via_false(self):
        from tools.mcp_tool import _load_tool_search_config
        config = {"mcp_tool_search": {"enabled": False}}
        with patch("hermes_cli.config.load_config", return_value=config):
            cfg = _load_tool_search_config()
        assert cfg["enabled"] is False

    def test_config_load_failure_returns_defaults(self):
        from tools.mcp_tool import _load_tool_search_config, _DEFAULT_TOOL_SEARCH_THRESHOLD
        with patch("hermes_cli.config.load_config", side_effect=RuntimeError("broken")):
            cfg = _load_tool_search_config()
        assert cfg["enabled"] == "auto"
        assert cfg["threshold"] == _DEFAULT_TOOL_SEARCH_THRESHOLD
