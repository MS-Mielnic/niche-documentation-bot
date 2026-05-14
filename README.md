# NicheDocBot 🤖

An advanced, UI-agnostic Agentic RAG (Retrieval-Augmented Generation) bot built with LangGraph, FastAPI, and local LLMs llama3.1, via Ollama. NicheDocBot is designed to dynamically search, ingest, and query GitHub repositories on demand, acting as an intelligent technical assistant. 

## 🗂️ Project Structure: Two Versions

This repository contains two distinct versions of the NicheDocBot, reflecting the evolution of the project as we encountered and overcame specific architectural challenges.

* **`src/` (Version 1):** The initial implementation of the Agentic RAG bot.
* **`src_v2/` (Version 2):** An evolved architecture designed to solve critical context fragmentation and retrieval issues discovered during the testing of Version 1.

---

### The Catalyst for Version 2: Diagnostics and Context Fragmentation

While Version 1 successfully implemented the core LangGraph state machine, GitHub ingestion, and local vector retrieval, rigorous testing revealed significant limitations in how the bot processed and retrieved context.

**The Diagnostic State (Version 1):**
Our logs showed that the system was technically sound:
* **Ingestion:** Successfully chunked, embedded, and stored repositories in ChromaDB.
* **State Management:** LangGraph's SQLite thread memory correctly persisted context across Slack turns.
* **Routing:** The LLM successfully classified intents (e.g., `KNOWLEDGE_QUERY`), with robust fallbacks handling minor formatting quirks.
* **Execution:** The system flawlessly executed the pipeline: intent classification, database match, and RAG generation.

**The Problem: The Sniper Missed the Target**
Despite the "plumbing" working perfectly, the *quality* of the RAG answers was poor. When a user asked for a "summary of the langgraph readme," the bot failed to return the root `README.md`. Instead, it retrieved deeply nested, peripheral files (like `libs/cli/README.md`) that happened to share keywords. The LLM, restricted by this poor context, would state that the main README was unavailable.

**The Root Cause:**
The issue was purely a **Retrieval Failure**, caused by two factors:
1.  **The Search Scope:** The database was restricted to a small number of chunks (e.g., `k=4`), which were quickly filled by irrelevant files sharing semantic keywords.
2.  **The Chunking Strategy:** The root `README.md` was chopped up in a way that destroyed its semantic meaning, making it "invisible" to a vector search looking for a general "summary."

**Version 2 (`src_v2/`)** was built specifically to address these challenges improved chunking strategies, and more sophisticated retrieval mechanisms.

---

## 🏗️ Master Architecture of the project v1 and v2

NicheDocBot is built around a robust, state-driven architecture ensuring clean separation of concerns and maximum reliability when running with local, compute-constrained LLMs.

### Core Pillars
1.  **UI-Agnostic Design (Ports & Adapters):** Communication protocols are decoupled from the agent logic. Interactions are handled via injected Adapters (e.g., `SlackAdapter` passed through `RunnableConfig`), allowing the bot to easily port to Teams, Discord, or web frontends without breaking the JSON-serializable graph state.
2.  **"Lazy Load" JIT Sync:** The bot tracks GitHub commit hashes locally. Before querying a repo, it checks if the remote branch has advanced (~100ms API call) and automatically performs a background delta sync if the codebase has changed.
3.  **Throttled, Structure-Aware Ingestion:** Shields the local LLM from massive context payloads. It pulls repo trees, processes Markdown/Text files incrementally, and uses strict `RecursiveCharacterTextSplitter` fallbacks to prevent Ollama context window overflows 
4.  **Human-in-the-Loop (HITL):** Uses LangGraph's `interrupt()` capability and SQLite checkpointing to safely put the graph to sleep while waiting for a user to select a repository via Slack interactive buttons.

## 🚀 Features

* **Intent Classification Routing (Node 0):** Prevents expensive vector searches for casual messages. A fast LLM gatekeeper classifies messages into `GREETING`, `KNOWLEDGE_QUERY`, or `NEW_REPO_REQUEST`.
* **Dynamic GitHub Integration:** Searches GitHub for requested technologies and fetches raw file structures directly via the GitHub REST API [cite: README.md].
* **100% Local & Private Processing:** Operates locally using **ChromaDB**, **Ollama** (`llama3.1` for text generation), and native **langchain-ollama** (`nomic-embed-text` for embeddings).
* **JSONL Telemetry Engine:** An integrated observability module (`src/observability/telemetry.py`) that passively logs retrieval distance scores, sources, and context windows to `rag_logs.jsonl` for offline RAG fine-tuning, retrieval alignment, and performance tracking [cite: README.md].

## 📂 Project Structure

```text
niche-doc-bot/
├── data/
│   ├── checkpoints.sqlite       # LangGraph persistent memory/state
│   ├── telemetry/
│   │   └── rag_logs.jsonl       # Observability logs for RAG performance
│   └── vector_db/               # Local ChromaDB collections per repo
├── src_v2/                      # contains v2 of the application
├── src/                         # 1st Version
│   ├── main.py                  # FastAPI server, Slack webhooks, and Orchestrator
│   ├── adapters/
|   |  └──base.py               # Base classes
│   │   └── slack.py             # BaseChatAdapter implementation for Slack UI
│   ├── graph/
│   │   ├── builder.py           # LangGraph StateGraph constructor & edges
│   │   ├── nodes.py             # Core agent logic (Intent, DB Check, RAG, GitHub Search)
│   │   └── state.py             # Pure JSON-serializable AgentState definition
│   ├── mcp/
│   │   └── github_client.py     # Async wrapper for GitHub API & JIT commit hashes
│   ├── observability/
│   │   └── telemetry.py         # JSONL logger for chunks, distance scores, and prompts
│   └── rag/
│       ├── ingestion.py         # Throttled parsing and chunking engine
│       └── vector_store.py      # ChromaDB setup and JIT Hash read/write utilities
└── .env                         # Environment variables (GitHub Token, Slack Secrets)
```

## 🧠 How the Graph Works (Node Workflow)

1.  **Node 0 (Classify Intent):** A lightweight prompt routes traffic based on intent. Stops the graph from doing heavy lifting for simple "Hellos" [cite: README.md].
2.  **Node 1 (Check Chroma DB):** Checks if the requested repo folder exists locally. Routes to Node 2 if data exists, or Node 3 if the DB is empty [cite: README.md].
3.  **Node 2 (Prompt for Query / RAG):** Queries ChromaDB using `similarity_search_with_score`. Constructs the context prompt, calls the LLM, logs the retrieval telemetry, and sends the answer to the user.
4.  **Node 3 (Search GitHub):** Extracts clean tech keywords from messy Slack messages, hits the GitHub Search API, and formats repository options [cite: README.md].
5.  **Node 4 (Wait for Human):** Sends a Slack button UI and triggers a LangGraph `interrupt()`. The graph serializes to SQLite and sleeps [cite: README.md]. Wakes up when the user clicks a repo button.
6.  **Node 5 (Throttled Ingestion / JIT Sync):** The engine. Performs a GitHub commit hash check. If the local hash matches GitHub, it skips ingestion [cite: README.md]. If different (or new), it safely ingests the repository, chunks the files, saves to Chroma, updates the local hash tracker, and notifies the user.

## 💻 Tech Stack
* **Orchestration:** LangGraph, LangChain
* **API Framework:** FastAPI
* **Vector Store:** ChromaDB
* **Local Inference:** Ollama (`llama3.1` for reasoning, `nomic-embed-text:latest` for native embeddings)
* **Data Parsing:** Unstructured
* **Integrations:** Slack WebClient, GitHub API

## 🛠️ Prerequisites

* **Python 3.10+** 
* **Ollama** running locally on port `11434`.
    * `ollama pull llama3.1` 
    * `ollama pull nomic-embed-text` 
* **GitHub Personal Access Token** (for bypassing API rate limits).
* **Slack App Credentials** (if using the Slack adapter).
* **tunnel** Slack's servers cannot send HTTP POST requests directly to our local localhost environment. By running ngrok http 8000 in the terminal, it creates a secure, publicly accessible URL (e.g., https://abc-123.ngrok-free.app) that routes directly to the local FastAPI server. Then the public URL generated can be used into Slack App's Event Subscriptions and Interactivity & Shortcuts dashboards so Slack knows exactly where to send the data.
## ⚙️ Setup & Installation

1.  Clone the repository and navigate into the directory .
2.  Install dependencies (requires `langchain`, `langgraph`, `langchain-ollama`, `langchain-chroma`, `unstructured`, `fastapi`, `uvicorn`, `httpx`).
3.  Set up your `.env` file with `GITHUB_TOKEN`.
4.  Run the API server:
    ```bash
    uvicorn src_v2.main:app --host 0.0.0.0 --port 8000
    ```

## 📊 Observability & Debugging

This bot implements a non-blocking Telemetry Engine. Instead of scattering `print()` statements, Node 2 silently writes a valid JSON object to `data/telemetry/rag_logs.jsonl` on every successful RAG generation.

You can load this file into Pandas or Jupyter Notebooks to monitor:
* **Distance Scores:** How mathematically close retrieved chunks are to the user's query.
* **Context Fragmentation:** Whether the chunking strategy is successfully grabbing core files (like `README.md`) or pulling irrelevant peripheral files.
* **System Drift:** How the retrieval quality changes as the ingested repositories evolve over time.

## How the RAG worked, or almost in V1 
Here is a breakdown of the Retrieval-Augmented Generation (RAG) architecture as it existed in Version 1 (src/) of the NicheDocBot.

While Version 1 successfully established the foundational "plumbing" of the bot, its design directly led to the retrieval limitations we eventually diagnosed and solved in Version 2.

**1. The Ingestion Engine (Data Preparation)**
The Version 1 ingestion pipeline was designed to pull files from GitHub and store them locally, shielding the LLM from massive payloads.

Fetching: It utilized the GitHub REST API to pull the directory tree and download raw .md and .txt files.

Parsing & Chunking: It relied on the unstructured library to partition Markdown files. To protect the local LLM's context window from overflowing (preventing 500 errors), it utilized a strict RecursiveCharacterTextSplitter as a fallback to forcefully chop large text elements into chunks of 2000 characters with a 200-character overlap.

Embedding & Storage: The chunks were vectorized using the local nomic-embed-text:latest model via Ollama and stored in a persistent, locally hosted ChromaDB collection specific to that repository.

**2. The Retrieval Mechanism (Node 2)**
When the LangGraph state machine (Node 0) classified a user's message as a KNOWLEDGE_QUERY, it routed to the retrieval node (Node 2).

Search Method: It executed a standard similarity_search against the ChromaDB collection.

The Constraint (k=4): The system was hardcoded to retrieve only the top 4 most mathematically similar chunks to the user's query.

Context Assembly: These 4 chunks were concatenated into a single text string, prepended with their source file paths, and injected into the prompt.

**3. The Generation Phase**
The LLM: The prompt, now loaded with the retrieved context and the user's original question, was sent to a local llama3.1 model running on Ollama.

The Output: The LLM synthesized the context to answer the user's technical question and passed the text back to the Slack Adapter.

The Fatal Flaw: Context Fragmentation
Technically, the Version 1 RAG system was completely operational—it did not crash, it successfully embedded data, and it generated answers. However, it suffered from a severe structural flaw known as Context Fragmentation.

Because the RecursiveCharacterTextSplitter chopped up crucial files (like the root README.md) without preserving their hierarchical or semantic meaning, those chunks lost their "weight" in the vector database.

When a user asked a broad question like "give me a summary of the langgraph readme", the database's strict k=4 limit was instantly filled up by irrelevant, deeply nested peripheral files (e.g., libs/cli/README.md) that happened to share the keyword "readme".

**The Result:** The system acted like a sniper missing the target. It handed the LLM the wrong context, forcing the LLM to hallucinate or apologize that the main README was "missing," directly prompting the architectural overhaul in Version 2.
