# NicheDocBot V2: Multimodal Agentic Repository RAG Bot

NicheDocBot V2 is an enterprise-grade, UI-agnostic, and structure-aware Retrieval-Augmented Generation (RAG) assistant built using **LangGraph** and **FastAPI**. It is engineered to perform local-first documentation parsing, repository extraction, and precision answer synthesis using open-weights models via Ollama.

This project houses both iterations of the application side-by-side to ensure architectural transparency and facilitate backward benchmarking:
* **Version 1 (`/src`):** Baseline prototype utilizing naive unstructured elements and text-only RAG loops.
* **Version 2 (`/src_v2`):** Production-ready Multimodal Analyst built using Inline Stateful Document Parsing, a localized parent-child key-value context bridge, and pixel-level evaluation.

---

## 🏗️ Architectural Evolution: Version 1 vs. Version 2

Version 2 was intentionally redesigned to target three systemic failure modes identified during production load-testing of the Version 1 prototype.

| Optimization Dimension | Version 1 Limitations (`/src`) | Version 2 Architecture (`/src_v2`) | Impact & Engineering Fix |
| :--- | :--- | :--- | :--- |
| **Parsing Strategy** | Naive Unstructured Loader Chunks (`unstructured.partition.md`)  | Precise **Deterministic Inline Stateful Block Parsing**  | **Eliminates Context Fragmentation:** Headings are no longer severed from paragraph text blocks. |
| **Markdown Tables** | Broken character-count slicing across cell/row data blocks  | Row-by-Row Index Vectors tagged with unique `parent_id` arrays  | **Zero Resolution Loss:** Searches pinpoint accurate rows, but the Interceptor inflates to full raw tables. |
| **Directory/ASCII Trees**| Chopped into separate arbitrary non-semantic elements  | Flattened to Absolute Path Key-Value Elements  | **Preserves Structural Hierarchy:** Retains critical layout whitespace/indentation for repository code layouts. |
| **Media Handling** | Completely "Blind Guide" loop; filters images or leaks raw alt text strings  | Automated GitHub binary stream pull with local Base64 compilation  | **True Visual Layout Parsing:** Elevates the system to see diagrams, charts, and layout structures. |
| **Ollama Model Core** | Text-only `Llama 3.1` (8B execution profile)  | Multimodal Vision-Enabled `Llama 3.2-Vision` (11B)  | **End-to-End Image Reasoning:** Can directly reason over, evaluate, and interpret document visual elements. |

---

## 🛠️ Deep Dive: Resolving Version 1 Structural Challenges

### 1. Context Fragmentation via Metadata-Triggered Parent Retrieval
* **The V1 Failure:** Relying on basic chunk splitters caused related elements to split randomly. For instance, a Markdown table row with a tool specification would lose its column headers, rendering the text mathematically uninterpretable to the LLM.
* **The V2 Resolution:** Ingestion utilizes custom regex inline sequential state blocks to isolate "Heavy components" (Tables, Trees, Images) out of standard text streams. Individual sub-elements are stored in ChromaDB to maximize search relevance (Sniper Approach), while their raw unadulterated blocks are serialized into a local disk-backed JSON Key-Value Store. At query time, a **Vision-Enabled Interceptor node** checks metadata, hooks the parent ID, and resolves the context matrix before generation.

### 2. Slack 3-Second Webhook Timeouts & Just-In-Time (JIT) Syncing
* **The V1 Failure:** Slack’s webhook integration enforces a strict **3-second HTTP response deadline**. Because local open-weights LLMs incur prompt processing overhead when analyzing a repository, the server would routinely trigger Slack automatic retry storm, launching infinite loops that hung the app.
* **The V2 Resolution:** FastAPI handles requests by immediately capturing incoming payloads, launching a non-blocking `BackgroundTasks` execution worker loop, and instantly releasing an `HTTP 200 OK` back to the Slack platform. Concurrently, the engine performs a lightning-fast GitHub commit hash check (~100ms). If hashes mismatch, it posts a typing notification ("*Hold on, I see new documentation...*") and applies streaming delta updates ("*Parsed 5/10 files...*") while updating indices.

### 3. Pure JSON Serializable States via Dependency Injection
* **The V1 Failure:** Placing functional client connection instances (e.g., live Slack/GitHub adapters) into the core LangGraph state broke graph checkpointing. The internal `SQLite Checkpointer` would crash when trying to serialize active class objects to disk.
* **The V2 Resolution:** Implemented a formal **Ports and Adapters architecture layout**. Communication endpoints conform to a strict abstraction instance (`BaseChatAdapter`). FastAPI instantiates runtime UI dependencies dynamically at runtime and injects them into the graph execution loop context using LangGraph's `RunnableConfig` dictionary. This preserves a 100% pure JSON-serializable `AgentState`.

---

## 📂 Project Directory Structure

```text
niche-bot/
├── .env                              # Local secure credentials environment configuration
├── .gitignore                        # Standard protection file (ignores checkpoints, dbs, and caches)
├── data/                             # Local Persistent Databases & Logs
    ├── checkpoints.sqlite            # Long-term state preservation SQLite checkpointer DB 
    ├── telemetry/                    # Observability workspace 
    │   └── rag_logs.jsonl            # Chronological JSONL log capture (metrics, scores, configurations) 
    ├── vector_db/                    # Legacy V1 Chroma Vector collections 
    └── vector_db_v2/                 # Evolved V2 Text + Multimodal Chroma collection databases 
        └── [repo_name]/parents/      # Local key-value JSON stores containing raw parent data blocks 
├── niche-bot-env/                    # Isolated Python virtual environment (.venv)
├── src/                              # Version 1 Architecture (Baseline prototype) 
│   ├── main.py                       # V1 Orchestrator API listener 
│   ├── adapters/                     # Text-only boundary ports (base.py, slack.py) 
│   ├── graph/                        # State graph definitions (builder.py, nodes.py, state.py) 
│   └── rag/                          # Naive chunking ingestion engine configurations 
└── src_v2/                           # Version 2 Architecture (Production Multimodal Analyst) 
    ├── main.py                       # Advanced FastAPI gateway with proxy routing adjustments 
    ├── adapters/                     # Refined communication and boundary contracts
    ├── mcp/                          # Model Context Protocol layer
    │   └── github_client.py          # Async binary image stream adapter & JIT hash verification 
    ── graph/                        # LangGraph orchestration state definitions
    │   ├── builder.py                # Graph structural map, edge conditional switches & checkpointers 
    │   ├── nodes.py                  # Brain logic nodes (Vision-Enabled Interceptor, Intent, Routing) 
    │   └── state.py                  # Pure JSON-serializable schema specifications 
    ├── observability/                # Performance tracking telemetry utilities 
    │   └── telemetry.py              # Performance tracking metrics hook engine 
    └── rag/                          # Advanced Ingestion Pipeline
        ├── parent_store.py           # Disk Key-Value asset serialization framework 
        ├── vector_store.py           # Localized Chroma DB configuration using nomic-embed-text 
        └── ingestion.py              # Structure-Aware parsing engine (Stateful Parsing & Regex)
```

        

## Core Agent Architecture & Graph Orchestration

NicheDocBot V2 is powered by a deterministic, cycle-permissible **LangGraph** state machine, replacing brittle linear LLM chains with structured runtime nodes and condition-driven routing paths. State durability is guaranteed via a pure, JSON-serializable `AgentState` schema anchored to an active `SqliteSaver` checkpointer database, allowing for seamless session recovery and non-blocking human-in-the-loop interaction gates. 

Adhering strictly to a modular **Ports and Adapters (Hexagonal) architecture design**, the underlying engine is completely decoupled from individual messaging platforms. The codebase uses Dependency Injection to supply a generic `BaseChatAdapter` interface to graph nodes at execution runtime. The repository includes a production-ready Slack adaptation framework as its default UI context, but the boundaries layer can be effortlessly extended to support alternative frontends like Discord, Teams, or local WebSockets without mutating the underlying graph logic.

### 🔄 The Execution Lifecycle Flow

The diagram below conceptualizes the structural choreography of the agentic graph, tracking messages from ingress to response formulation:

```text
       [ Incoming Payload ]
                 │
                 ▼
       ┌───────────────────┐
       │ 1. Classification │ ──► (Determines user intent)
       └───────────────────┘
                 │
                 ▼
       ┌───────────────────┐
       │   2. Hash Check   │ ──► [ Hashes Differ ] ──► ┌──────────────────────┐
       └───────────────────┘                           │ 3. Immediate Ack /   │
                 │                                     │    Background Sync   │
         [ Hashes Match ]                              └──────────────────────┘
                 │                                                 │
                 ▼ ◄───────────────────────────────────────────────┘
       ┌───────────────────┐
       │  4. Interceptor   │ ──► (Extracts targeted Chroma row vectors &
       └───────────────────┘      reconstructs complete Parent Blocks from Disk)
                 │
                 ▼
       ┌───────────────────┐
       │  5. Router Node   │
       └───────────────────┘
                 │
         ┌───────┴───────┐
         ▼               ▼
   [ Text Route ]  [ Vision Route ]
         │               │
         ▼               ▼
 ┌──────────────┐┌──────────────┐
 │ 6. Text LLM  ││  7. Llama    │
 │ (Llama 3.1)  ││  3.2-Vision  │
 └──────────────┘└──────────────┘
         │               │
         └───────┬───────┘
                 │
                 ▼
       ┌───────────────────┐
       │   8. Wait for human Check   │ ──► [asynchronous user interaction and repository selection]
       └───────────────────┘                                                   │
                 │                                                             ▼
          [ Ready / Clean ]                                        ┌────────────────────────┐
                 │                                                 │  9. Process Override   │
                 ▼                                                 └────────────────────────┘
       ┌───────────────────┐                                                    │
       │ 10. Agnostic Dispatch│ ◄───────────────────────────────────────────────┘
       └───────────────────┘
```
---
## Detailed Node Breakdown
1. **Intent Classification (classify_intent_node)
Objective:** Intercepts raw user text input and normalizes it into structural intent primitives to optimize downstream execution pathways.

* **Logic:**  Employs an ultra-fast local LLM prompt map to evaluate whether the query represents a structural code/documentation question (KNOWLEDGE_QUERY), an active infrastructure state change request, or a general conversational element.

2. **Just-In-Time Hash Check (jit_hash_check_node)
Objective**: Determines if the locally cached vector databases are actively synchronized with the target remote repository state without triggering Slack webhook timeouts.

* **Logic:** Issues an explicit HEAD request to fetch the remote repository's latest commit hash (~100ms processing footprint).If hashes match, the graph proceeds immediately to the context compilation layer.
If hashes differ, it diverges to the Immediate Acknowledgment / Background Sync Loop.

3. **Immediate Acknowledgment & Background Ingestion (throttled_ingestion_node)
Objective:** Mitigates Slack's strict 3-second response window by decoupling HTTP network requests from local heavy indexing processing loops.

* **Logic:** Returns an instantaneous HTTP 200 OK handshake string frame to Slack while deploying an isolated background asynchronous worker. The worker prints tracking logs to the user interface ("Hold on, I see new documentation..."), clones the changes, applies the custom stateful regex parser to extract text blocks, and commits updated parent block indices into storage.

4. **The Vision & Layout Interceptor (interceptor_node)
Objective:** Resolves the severe structural text-splitting context fragmentation found in Version 1.

* **Logic:** Performs a tight mathematical vector distance search over the local ChromaDB schema. Instead of forwarding character-split strings directly to the generation model, the Interceptor inspects chunk metadata. If it encounters a structural pointer (such as an individual row from a complex Markdown matrix), it accesses the local disk-backed Key-Value store to inject the entire unadulterated parent block layout back into context before synthesis.

5. **Dynamic Routing Gate (routing_router_node)
Objective:** Routes data based on the underlying media configuration types discovered within the parsed context assets.

* **Logic:** Evaluates file structure formats. If target documents consist exclusively of narrative Markdown or text strings, it maps requests directly to the standard generation node. If the query uncovers code visual flowcharts, wireframe paths, architectural assets, or binary image types, it routes the payload to the Vision node.

6. **Text Synthesis Node (text_generation_node)
Objective:** Generates concise analytical answers from clean text payloads.

* **Logic:** Processes questions using local context via Llama 3.1, applying strict system prompts to prevent hallucinations.

7. **Multimodal Vision Node (vision_reasoning_node)
Objective:** Interprets media, graphs, formatting layouts, and user interface pixel architectures.

* **Logic:** Compiles uncorrupted binary byte streams derived from the repository, transforms them into standardized Base64 string models, and maps them to Llama 3.2-Vision. This allows the system to visually inspect actual file designs, flow charts, or layout diagrams.

8. **Asynchronous UI Synchronization (`wait_for_human_node`) Objective:** Manages asynchronous user interaction and repository selection without causing thread blocks or infinite retry loops.
* **Logic:** When the graph requires explicit user guidance (such as selecting a target repository from a list or confirming a follow-up query path), the node interrupts the active LangGraph execution thread. It serializes the current `AgentState` to the SQLite checkpointer database, renders interactive choice components directly into the Slack UI, and completely halts execution. The graph remains safely suspended until the user interacts with the UI, which re-activates the state machine via the `/slack/interactions` gateway.

9. **State Execution Override (process_override_node)
Objective:** Translates human button interactions back into actionable state structures.

* **Logic:** Consumes the human workspace decision payload, applies input corrections, overwrites target state variables, and updates execution vectors to route the model smoothly back to dispatch.

10. **Unified Interface Dispatch Node (`prompt_for_query` Output Cycle)
Objective:** Dispatches finalized content blocks and dynamic multi-media attachments back to the originating client window while remaining completely decoupled from specific frontend platforms.
* **Logic:** The node accesses a generic `chat_adapter` instance provided dynamically at runtime via LangGraph's `RunnableConfig` dictionary. It compiles the local text response strings and arrays of raw media asset paths into standardized payloads. It then executes a single polymorphic connection method call (`await chat_adapter.send_message(...)`), delegating platform-specific formatting conversion tasks (e.g., Slack Blocks, Discord Markdown, or custom WebSocket JSON formats) entirely to the boundaries layer.