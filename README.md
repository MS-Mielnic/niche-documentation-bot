# Niche Documentation Bot

A sophisticated codebase and documentation assistant that allows users to ask natural language questions in Slack and receive precise, factual answers based on frequently updated Markdown documentation.

## Architecture 

* **The Pipeline**: We are building a straightforward, linear RAG pipeline using LangChain. Standard Retrieval-Augmented Generation is fundamentally a linear process where data flows from start to finish without looping back, making LangChain the perfect orchestrator.
* **Integration Layer**: A GitHub MCP server will act as the bridge. The MCP server securely hosts the data connections, executes read requests against the GitHub API, and uniformly returns the raw codebase context to LangChain.
* **Vector Database**: We are utilizing ChromaDB for our local smart index. ChromaDB is ideal for prototyping due to its developer velocity and its built-in persistence, which automatically saves embeddings to a local file system out-of-the-box.
* **Reasoning Engine**: We will use a local model, such as Llama 3. Running locally ensures the system is completely free and 100% private.
* **Retrieval Strategy**: We are implementing an advanced retrieval pipeline featuring a Re-ranker. The Re-ranker scores retrieved chunks and filters out all but the absolute best, ensuring the LLM is only handed the most concentrated, highly relevant information.
* **User Interface**: The final bot will feature a Slack integration. This allows for a seamless interaction where the user asks a natural language question in Slack and gets a precise answer.

## Implementation Phases
1. **Phase 1**: Setting up the Model Context Protocol server to securely connect to the public GitHub repository and fetch the raw Markdown files.
2. **Phase 2**: Building the linear LangChain pipeline that receives a query, runs a broad semantic search against ChromaDB, passes the chunks through the lightweight Re-ranker, and hands the best filtered chunks to the LLM to synthesize an answer.
3. **Phase 3**: Connecting the finished LangChain pipeline to a Slack application to listen for natural language questions and deliver the precise answers.# Niche Documentation Bot

