### NicheDocBot V2: Multimodal Agentic Repository RAG Bot
**NicheDocBot V2** is an enterprise-grade, UI-agnostic, and structure-aware Retrieval-Augmented Generation (RAG) assistant built using LangGraph and FastAPI. It is engineered to perform local-first documentation parsing, repository extraction, and precision answer synthesis using open-weights models via Ollama.
This project houses both iterations of the application side-by-side to ensure architectural transparency and facilitate backward benchmarking:
* **Version 1 (/src)** Baseline prototype utilizing naive unstructured elements and text-only RAG loops.
* **Version 2 (/src_v2):** Production-ready Multimodal Analyst built using Inline Stateful Document Parsing, a localized parent-child key-value context bridge, and pixel-level evaluation.

## 🏗️ Architectural Evolution: Version 1 vs. Version 2

| Optimization Dimension | Version 1 (`/src`) | Version 2 (`/src_v2`) | Impact & Engineering Fix |
| :--- | :--- | :--- | :--- |
| **Parsing Strategy** | Naive `unstructured.partition.md` chunks | Deterministic Inline Stateful Block Parsing | Eliminates context fragmentation; headings and blocks remain intact. |
| **Markdown Tables** | Broken character-count slicing | Parent-Child KV Store | Semantic structural retention (Tables treated as objects). |
| **UI/UX Updates** | Silent or blocking ingestion | Async In-place Slack Updates | Real-time progress feedback using message pointers. |
| **Threading** | Main-channel message leaks | Context-Aware Slack Adapters | 100% thread isolation (`thread_ts` enforcement). |
| **Multimodal** | Text-only retrieval | Vision-Language Synthesis | Visual reasoning enabled via `llama3.2-vision`. |

## 📁 Repository Directory Tree (src_v2/)Plaintextsrc_v2/
```text
├── main.py             # FastAPI Gateway: Webhook ingestion & Graph state loader
├── graph/              # Orchestration Layer
│   ├── builder.py      # Workflow graph compilation
│   ├── nodes.py        # Core functional logic execution (Nodes 0-5)
│   └── state.py        # Strict TypedDict state schema
├── rag/                # Ingestion & Retrieval Logic
│   ├── ingestion.py    # Stateful file parsing & JIT progress updates
│   ├── vector_store.py # ChromaDB interactions & hash management
│   └── parent_store.py # KV store for non-chunkable Markdown tables/trees
├── adapters/           # UI Ports & Adapters Layer
│   ├── base.py         # Abstract Base Class for UI-agnosticism
│   └── slack.py        # Slack-specific implementation (Block Kit)
└── observability/      # Telemetry & Health Monitoring
```


## 🔄 Execution Lifecycle Flow
The system processes requests via a non-blocking asynchronous pipeline designed to handle Slack's strict response deadlines:

1. **Webhook Ingress:** The main.py gateway captures Slack event payloads (/slack/events or /slack/interactions).
2. **Thread Initialization:** A unique langgraph_thread_id (Channel + TS) is generated, ensuring conversation memory isolation via SqliteSaver.
3. **State Hand-off:** The request is mapped to an AgentState object, explicitly passing channel_id (for Slack targeting) and thread_ts (for thread containerization).
4. **Graph Orchestration:** The state machine traverses the node sequence, updating the persistent state at every transition.
5. **Human-in-the-loop (HITL):** If user input is required (e.g., repo selection), the graph triggers an interrupt(), halting state machine execution until the user interacts with the UI.
6. **Asynchronous Resumption:** The gateway resumes the graph using the checkpointed state, maintaining a seamless user experience.

## 🛠️ Detailed Node Breakdown

| Node | Name | Responsibility | Logic / Key Mechanism |
| :--- | :--- | :--- | :--- |
| **Node 0** | `classify_intent` | Intent Routing | LLM-based classification (Greeting, Query, or Repo Request) to minimize overhead. |
| **Node 1** | `check_chroma_db` | Validation | Hash-based verification; determines if a local index exists or requires JIT re-sync. |
| **Node 2** | `prompt_for_query` | Hybrid Interceptor | Performs hybrid vector search, reconstructs tables/trees from KV store, and routes to Vision LLM. |
| **Node 3** | `search_github` | Repository Discovery | MCP-driven search via GitHub API; dispatches Slack Block Kit UI for user approval. |
| **Node 4** | `wait_for_human` | State Suspension | `langgraph.interrupt` barrier; halts state machine execution until interaction occurs. |
| **Node 5** | `throttled_ingestion` | Sync Engine | JIT commit-hash validation; sequential ingestion loop with in-place Slack progress updates. |

## 🚀 Key Engineering Features
### Thread-Safe Slack Communication
By leveraging a Polymorphic Adapter Layer (BaseChatAdapter), the bot decouples graph logic from UI delivery. Every message in V2 is injected with the correct thread_ts derived from the initial event, ensuring the bot never leaks content into the main workspace channel.
### "Lazy Load" JIT Sync Engine
Replacement of the full re-ingestion with a JIT (Just-In-Time) Hash Check. The system calculates the repository's commit hash upon request; if the hash matches the local state, we skip ingestion entirely, reducing sync latency from minutes to milliseconds.
### Structural Integrity (Interceptor Pattern)
Version 2 uses a hybrid parsing engine. Markdown tables and directory trees are flattened into "Sniper Documents" during ingestion. At query time, Node 2 reconstructs these objects, ensuring the LLM sees the complete structure, not just disconnected chunks.

## 🚀 Setup & Execution
Prerequisites
* **macOS:** Apple Silicon (M3 Max / 64GB recommended).
* **Local LLM Engine (Ollama running):**
    * llama3.2-vision:latest (Vision/Reasoning)
    * llama3.1:latest (Text Synthesis)
    * nomic-embed-text:latest (Vector Embeddings)
* **Tunneling:** ngrok (mapping local :8000 to Slack App Events).
### Quickstart
1. **Configure Environment:** Create a .env file with SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, and GITHUB_TOKEN.
2. **Launch Proxy:** Run ngrok http 8000 and copy the URL into your Slack App Dashboard Event Subscriptions.
3. **Launch Engine:**
```text
 uvicorn src_v2.main:app --reload --port 8000.
 ```