---
artifact_type: raw
source_slug: mcp-spec
source_url_primary: https://modelcontextprotocol.io/introduction
source_urls:
  - https://modelcontextprotocol.io/introduction
  - https://modelcontextprotocol.io/docs/learn/architecture
  - https://modelcontextprotocol.io/docs/learn/server-concepts
fetched_at: 2026-05-07
fetched_via: claude_code_webfetch
mcp_protocol_version_seen: "2025-06-18"
---

# MCP 官方 docs 抓取归档（2026-05-07）

本文件是 `wiki/sources/mcp-spec.md` 的 raw 归档。三页官方 docs 内容按 fetch 顺序整理；任何引用本归档的论断必须能定位到对应小节。

---

## §1. modelcontextprotocol.io/introduction（fetched 2026-05-07）

### 定义（原文）

> "MCP (Model Context Protocol) is an open-source standard for connecting AI applications to external systems."

> "Using MCP, AI applications like Claude or ChatGPT can connect to data sources (e.g. local files, databases), tools (e.g. search engines, calculators) and workflows (e.g. specialized prompts)—enabling them to access key information and perform tasks."

### 核心类比（原文）

> "Think of MCP like a USB-C port for AI applications. Just as USB-C provides a standardized way to connect electronic devices, MCP provides a standardized way to connect AI applications to external systems."

### MCP 能做什么（原文清单）

- "Agents can access your Google Calendar and Notion, acting as a more personalized AI assistant."
- "Claude Code can generate an entire web app using a Figma design."
- "Enterprise chatbots can connect to multiple databases across an organization, empowering users to analyze data using chat."
- "AI models can create 3D designs on Blender and print them out using a 3D printer."

### 三方价值（原文）

| 角色 | 价值（原文） |
|---|---|
| Developers | "MCP reduces development time and complexity when building, or integrating with, an AI application or agent." |
| AI applications or agents | "MCP provides access to an ecosystem of data sources, tools and apps which will enhance capabilities and improve the end-user experience." |
| End-users | "MCP results in more capable AI applications or agents which can access your data and take actions on your behalf when necessary." |

### 生态支持（原文）

> "MCP is an open protocol supported across a wide range of clients and servers. AI assistants like Claude and ChatGPT, development tools like Visual Studio Code, Cursor, MCPJam, and many others all support MCP — making it easy to build once and integrate everywhere."

---

## §2. modelcontextprotocol.io/docs/learn/architecture（fetched 2026-05-07）

### 范围声明（原文）

> "MCP focuses solely on the protocol for context exchange—it does not dictate how AI applications use LLMs or manage the provided context."

### 三类参与者（原文）

> "MCP follows a client-server architecture where an MCP host — an AI application like Claude Code or Claude Desktop — establishes connections to one or more MCP servers. The MCP host accomplishes this by creating one MCP client for each MCP server. Each MCP client maintains a dedicated connection with its corresponding MCP server."

> "Local MCP servers that use the STDIO transport typically serve a single MCP client, whereas remote MCP servers that use the Streamable HTTP transport will typically serve many MCP clients."

| 参与者 | 定义（原文） |
|---|---|
| MCP Host | "The AI application that coordinates and manages one or multiple MCP clients" |
| MCP Client | "A component that maintains a connection to an MCP server and obtains context from an MCP server for the MCP host to use" |
| MCP Server | "A program that provides context to MCP clients" |

VS Code 实例（原文）：

> "Visual Studio Code acts as an MCP host. When Visual Studio Code establishes a connection to an MCP server, such as the Sentry MCP server, the Visual Studio Code runtime instantiates an MCP client object that maintains the connection to the Sentry MCP server. When Visual Studio Code subsequently connects to another MCP server, such as the local filesystem server, the Visual Studio Code runtime instantiates an additional MCP client object to maintain this connection."

### 两层架构（原文）

> "MCP consists of two layers:
> - Data layer: Defines the JSON-RPC based protocol for client-server communication, including lifecycle management, and core primitives, such as tools, resources, prompts and notifications.
> - Transport layer: Defines the communication mechanisms and channels that enable data exchange between clients and servers, including transport-specific connection establishment, message framing, and authorization."

### Data layer（原文 4 项）

> "The data layer implements a JSON-RPC 2.0 based exchange protocol that defines the message structure and semantics. This layer includes:
> - Lifecycle management: Handles connection initialization, capability negotiation, and connection termination between clients and servers
> - Server features: Enables servers to provide core functionality including tools for AI actions, resources for context data, and prompts for interaction templates from and to the client
> - Client features: Enables servers to ask the client to sample from the host LLM, elicit input from the user, and log messages to the client
> - Utility features: Supports additional capabilities like notifications for real-time updates and progress tracking for long-running operations"

### Transport layer（原文 2 种）

> "MCP supports two transport mechanisms:
> - Stdio transport: Uses standard input/output streams for direct process communication between local processes on the same machine, providing optimal performance with no network overhead.
> - Streamable HTTP transport: Uses HTTP POST for client-to-server messages with optional Server-Sent Events for streaming capabilities. This transport enables remote server communication and supports standard HTTP authentication methods including bearer tokens, API keys, and custom headers. MCP recommends using OAuth to obtain authentication tokens."

### Lifecycle 关键描述（原文）

> "MCP is a stateful protocol that requires lifecycle management. The purpose of lifecycle management is to negotiate the capabilities that both client and server support."

### Server primitives（原文 3 类）

> "MCP defines three core primitives that servers can expose:
> - Tools: Executable functions that AI applications can invoke to perform actions (e.g., file operations, API calls, database queries)
> - Resources: Data sources that provide contextual information to AI applications (e.g., file contents, database records, API responses)
> - Prompts: Reusable templates that help structure interactions with language models (e.g., system prompts, few-shot examples)"

> "Each primitive type has associated methods for discovery (`*/list`), retrieval (`*/get`), and in some cases, execution (`tools/call`). MCP clients will use the `*/list` methods to discover available primitives."

### Client primitives（原文 3 类）

> "MCP also defines primitives that clients can expose. These primitives allow MCP server authors to build richer interactions.
> - Sampling: Allows servers to request language model completions from the client's AI application. This is useful when server authors want access to a language model, but want to stay model-independent and not include a language model SDK in their MCP server.
> - Elicitation: Allows servers to request additional information from users. This is useful when server authors want to get more information from the user, or ask for confirmation of an action.
> - Logging: Enables servers to send log messages to clients for debugging and monitoring purposes."

### Notifications（原文）

> "The protocol supports real-time notifications to enable dynamic updates between servers and clients. For example, when a server's available tools change—such as when new functionality becomes available or existing tools are modified—the server can send tool update notifications to inform connected clients about these changes."

### Lifecycle 示例摘录（原文 4 步）

> "Step 1: Initialization (Lifecycle Management) — MCP begins with lifecycle management through a capability negotiation handshake. Client sends `initialize` request to establish the connection and negotiate supported features."

Step 1 完整 JSON 示例（原文）：

```json
// Initialize Request
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {
      "elicitation": {}
    },
    "clientInfo": {
      "name": "example-client",
      "version": "1.0.0"
    }
  }
}

// Initialize Response
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": {
      "tools": {
        "listChanged": true
      },
      "resources": {}
    },
    "serverInfo": {
      "name": "example-server",
      "version": "1.0.0"
    }
  }
}
```

> "After successful initialization, the client sends a notification to indicate it's ready: `{ jsonrpc: 2.0, method: notifications/initialized }`"

Step 1 三大目的（原文）：

> "1. Protocol Version Negotiation: The protocolVersion field (e.g., '2025-06-18') ensures both client and server are using compatible protocol versions. This prevents communication errors that could occur when different versions attempt to interact. If a mutually compatible version is not negotiated, the connection should be terminated.
> 2. Capability Discovery: The capabilities object allows each party to declare what features they support, including which primitives they can handle (tools, resources, prompts) and whether they support features like notifications.
> 3. Identity Exchange: The clientInfo and serverInfo objects provide identification and versioning information for debugging and compatibility purposes."

示例 capability negotiation 含义（原文）：

> "Client Capabilities: 'elicitation: {}' - The client declares it can work with user interaction requests (can receive elicitation/create method calls). Server Capabilities: 'tools: {listChanged: true}' - The server supports the tools primitive AND can send tools/list_changed notifications when its tool list changes. 'resources: {}' - The server also supports the resources primitive (can handle resources/list and resources/read methods)."

> "Step 2: Tool Discovery (Primitives) — Client can discover available tools by sending a `tools/list` request."
> 
> "Step 3: Tool Execution (Primitives) — The client can now execute a tool using the `tools/call` method."
> 
> "Step 4: Real-time Updates (Notifications) — When the server's available tools change, the server can proactively notify connected clients via `notifications/tools/list_changed`."

---

## §3. modelcontextprotocol.io/docs/learn/server-concepts（fetched 2026-05-07）

### Server 三大 building blocks（原文表格）

| Feature | Explanation（原文） | 控制方 |
|---|---|---|
| Tools | "Functions that your LLM can actively call, and decides when to use them based on user requests. Tools can write to databases, call external APIs, modify files, or trigger other logic." | Model |
| Resources | "Passive data sources that provide read-only access to information for context, such as file contents, database schemas, or API documentation." | Application |
| Prompts | "Pre-built instruction templates that tell the model to work with specific tools and resources." | User |

### Tools（原文要点）

> "Tools enable AI models to perform actions. Each tool defines a specific operation with typed inputs and outputs. The model requests tool execution based on context."

> "Tools are schema-defined interfaces that LLMs can invoke. MCP uses JSON Schema for validation. Each tool performs a single operation with clearly defined inputs and outputs. Tools may require user consent prior to execution, helping to ensure users maintain control over actions taken by a model."

Protocol operations（原文）：

| Method | Purpose | Returns |
|---|---|---|
| `tools/list` | Discover available tools | Array of tool definitions with schemas |
| `tools/call` | Execute a specific tool | Tool execution result |

User Interaction Model（原文）：

> "Tools are model-controlled, meaning AI models can discover and invoke them automatically. However, MCP emphasizes human oversight through several mechanisms.
> For trust and safety, applications can implement user control through various mechanisms, such as:
> - Displaying available tools in the UI, enabling users to define whether a tool should be made available in specific interactions
> - Approval dialogs for individual tool executions
> - Permission settings for pre-approving certain safe operations
> - Activity logs that show all tool executions with their results"

### Resources（原文要点）

> "Resources provide structured access to information that the AI application can retrieve and provide to models as context."

> "Resources expose data from files, APIs, databases, or any other source that an AI needs to understand context. Applications can access this information directly and decide how to use it - whether that's selecting relevant portions, searching with embeddings, or passing it all to the model."

> "Each resource has a unique URI (e.g., `file:///path/to/document.md`) and declares its MIME type for appropriate content handling."

User Interaction Model（原文）：

> "Resources are application-driven, giving them flexibility in how they retrieve, process, and present available context."

### Prompts（原文要点）

> "Prompts provide reusable templates. They allow MCP server authors to provide parameterized prompts for a domain, or showcase how to best use the MCP server."

> "Prompts are structured templates that define expected inputs and interaction patterns. They are user-controlled, requiring explicit invocation rather than automatic triggering."

User Interaction Model（原文）：

> "Prompts are user-controlled, requiring explicit invocation. Applications typically expose prompts through various UI patterns such as:
> - Slash commands (typing '/' to see available prompts like /plan-vacation)
> - Command palettes for searchable access
> - Dedicated UI buttons for frequently used prompts
> - Context menus that suggest relevant prompts"

### Multi-Server 协作示例（原文摘录）

> "The real power of MCP emerges when multiple servers work together, combining their specialized capabilities through a unified interface."

> "Example: Multi-Server Travel Planning — Travel Server (flights, hotels, itineraries) + Weather Server (climate data) + Calendar/Email Server (schedules, communications)."

完整流程：用户调 prompt → 用户选 resources → AI 用 tools 执行（searchFlights / checkWeather / bookHotel / createCalendarEvent / sendEmail）。

---

## §4. 抓取元信息

| 字段 | 值 |
|---|---|
| fetched_at | 2026-05-07 |
| fetched_via | Claude Code WebFetch（每页一次 prompt 提取，原文已尽量保留） |
| MCP protocol version 提及 | `2025-06-18`（在 architecture 页的 lifecycle 示例中） |
| 引用方 source 页 | `wiki/sources/mcp-spec.md` |
| 引用方 concept 页 | `wiki/concepts/mcp-tool-use.md` |
