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