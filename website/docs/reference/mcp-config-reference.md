---
sidebar_position: 8
title: "MCP Config Reference"
description: "Reference for Hermes Agent MCP configuration keys, filtering semantics, and utility-tool policy"
---

# MCP Config Reference

This page is the compact reference companion to the main MCP docs.

For conceptual guidance, see:
- [MCP (Model Context Protocol)](/docs/user-guide/features/mcp)
- [Use MCP with Hermes](/docs/guides/use-mcp-with-hermes)

## Root config shape

```yaml
mcp_servers:
  <server_name>:
    command: "..."      # stdio servers
    args: []
    env: {}

    # OR
    url: "..."          # HTTP servers
    headers: {}

    enabled: true
    timeout: 120
    connect_timeout: 60
    tools:
      include: []
      exclude: []
      resources: true
      prompts: true

mcp_tool_search:          # deferred tool loading
  enabled: auto           # auto | true | false
  threshold: 20           # tool count trigger for auto mode
```

## Server keys

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `command` | string | stdio | Executable to launch |
| `args` | list | stdio | Arguments for the subprocess |
| `env` | mapping | stdio | Environment passed to the subprocess |
| `url` | string | HTTP | Remote MCP endpoint |
| `headers` | mapping | HTTP | Headers for remote server requests |
| `enabled` | bool | both | Skip the server entirely when false |
| `timeout` | number | both | Tool call timeout |
| `connect_timeout` | number | both | Initial connection timeout |
| `tools` | mapping | both | Filtering and utility-tool policy |

## `tools` policy keys

| Key | Type | Meaning |
|---|---|---|
| `include` | string or list | Whitelist server-native MCP tools |
| `exclude` | string or list | Blacklist server-native MCP tools |
| `resources` | bool-like | Enable/disable `list_resources` + `read_resource` |
| `prompts` | bool-like | Enable/disable `list_prompts` + `get_prompt` |

## Filtering semantics

### `include`

If `include` is set, only those server-native MCP tools are registered.

```yaml
tools:
  include: [create_issue, list_issues]
```

### `exclude`

If `exclude` is set and `include` is not, every server-native MCP tool except those names is registered.

```yaml
tools:
  exclude: [delete_customer]
```

### Precedence

If both are set, `include` wins.

```yaml
tools:
  include: [create_issue]
  exclude: [create_issue, delete_issue]
```

Result:
- `create_issue` is still allowed
- `delete_issue` is ignored because `include` takes precedence

## Utility-tool policy

Hermes may register these utility wrappers per MCP server:

Resources:
- `list_resources`
- `read_resource`

Prompts:
- `list_prompts`
- `get_prompt`

### Disable resources

```yaml
tools:
  resources: false
```

### Disable prompts

```yaml
tools:
  prompts: false
```

### Capability-aware registration

Even when `resources: true` or `prompts: true`, Hermes only registers those utility tools if the MCP session actually exposes the corresponding capability.

So this is normal:
- you enable prompts
- but no prompt utilities appear
- because the server does not support prompts

## Deferred tool loading (Tool Search)

When many MCP tools are configured, their schemas can consume a large portion of the context window on every API call. Hermes can automatically defer MCP tools and expose a single `search_mcp_tools` meta-tool instead. The agent uses it to discover and activate only the tools it needs.

### How it works

1. All MCP servers connect and discover tools at startup (unchanged)
2. If the total MCP tool count exceeds the threshold, all MCP tools are deferred
3. A `search_mcp_tools` tool is registered in their place
4. The agent calls `search_mcp_tools(query="stripe payments")` to find relevant tools
5. Matched tools are activated and available from the next turn onward
6. Activated tools persist for the current session (reset on new thread, `/reset`, or cron job)

### Config

```yaml
mcp_tool_search:
  enabled: auto    # auto | true | false (default: auto)
  threshold: 20    # only used in auto mode (default: 20)
```

| Value | Behavior |
|---|---|
| `auto` | Defer when total MCP tools exceed `threshold` |
| `true` | Always defer, regardless of tool count |
| `false` | Never defer (all tools loaded upfront, original behavior) |

### Search scoring

`search_mcp_tools` uses word-boundary-aware keyword matching. Tool names are split on `_` into words, and each query term is scored:

| Match type | Name weight | Description weight |
|---|---|---|
| Exact word | 5 | 2 |
| Word prefix (≥3 chars) | 4 | 1 |
| Substring | 2 | 1 |

Only the top `max_results` (default 5) tools are activated per search call. Multi-term queries accumulate scores, so `"stripe list"` strongly favors `mcp_stripe_list_customers` over `mcp_github_list_repos`.

### Session scoping

Activated tools are scoped to the current session. Each parallel session (Discord thread, cron job, background agent) maintains its own set of activated tools — tools activated in one session do not appear in another session's context.

Subagents spawned via `delegate_task` inherit the parent session's activated tools.

Within a session, activated tools persist across multiple messages — the agent does not need to re-search between turns in the same conversation.

The gateway can optionally call `cleanup_session(session_id)` when a session expires to free registry entries for tools no longer referenced by any active session.

### Example

With 260 MCP tools across 6 servers:

```
# Before: every API call sends 260 tool schemas (~80k+ tokens)
# After:  most calls send ~20 built-in tools + search_mcp_tools

Agent: search_mcp_tools(query="stripe customers")
→ Activates: mcp_stripe_list_customers, mcp_stripe_create_customer, ...

Agent: mcp_stripe_list_customers(limit=10)
→ Works normally — tool is now in the active set
```

## `enabled: false`

```yaml
mcp_servers:
  legacy:
    url: "https://mcp.legacy.internal"
    enabled: false
```

Behavior:
- no connection attempt
- no discovery
- no tool registration
- config remains in place for later reuse

## Empty result behavior

If filtering removes all server-native tools and no utility tools are registered, Hermes does not create an empty MCP runtime toolset for that server.

## Example configs

### Safe GitHub allowlist

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, update_issue, search_code]
      resources: false
      prompts: false
```

### Stripe blacklist

```yaml
mcp_servers:
  stripe:
    url: "https://mcp.stripe.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      exclude: [delete_customer, refund_payment]
```

### Resource-only docs server

```yaml
mcp_servers:
  docs:
    url: "https://mcp.docs.example.com"
    tools:
      include: []
      resources: true
      prompts: false
```

## Reloading config

After changing MCP config, reload servers with:

```text
/reload-mcp
```

## Tool naming

Server-native MCP tools become:

```text
mcp_<server>_<tool>
```

Examples:
- `mcp_github_create_issue`
- `mcp_filesystem_read_file`
- `mcp_my_api_query_data`

Utility tools follow the same prefixing pattern:
- `mcp_<server>_list_resources`
- `mcp_<server>_read_resource`
- `mcp_<server>_list_prompts`
- `mcp_<server>_get_prompt`
