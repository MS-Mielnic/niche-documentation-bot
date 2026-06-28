"""
Splunk / OpenTelemetry GenAI telemetry helpers for NicheDocBot.

This module centralizes the Splunk GenAI utility usage so graph nodes do not
need to know the low-level handler API directly.

Runtime note:
The Docker/Kubernetes runtime for splunk-otel-util-genai==0.1.14 exposes
context-manager methods on the telemetry handler:
- workflow(...)
- invoke_local_agent(...)
- tool(...)
- start_llm(...)
- stop_llm(...)

It does not expose RetrievalInvocation or Step in this runtime API.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from opentelemetry import trace
from opentelemetry.util.genai.handler import get_telemetry_handler
from opentelemetry.util.genai.types import Error, InputMessage, OutputMessage, Text


GENAI_WORKFLOW_NAME = "nichedocbot.repo_rag_answer"
GENAI_AGENT_NAME = "NicheDocBot"
GENAI_FRAMEWORK = "langgraph"
GENAI_SYSTEM = "ollama"
OLLAMA_SERVER_ADDRESS = "host.docker.internal"
OLLAMA_SERVER_PORT = 11434


def genai_enabled() -> bool:
    """
    Return whether Splunk GenAI telemetry helpers should emit telemetry.

    Enabled by default because the dependency is now explicit. It can be
    disabled with NICHE_GENAI_TELEMETRY_ENABLED=false if troubleshooting.
    """
    return os.getenv("NICHE_GENAI_TELEMETRY_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
    }


def text_input(role: str, content: str) -> InputMessage:
    """Create a GenAI input message with a single text part."""
    return InputMessage(role=role, parts=[Text(content=content)])


def text_output(role: str, content: str, finish_reason: Optional[str] = None) -> OutputMessage:
    """Create a GenAI output message with a single text part."""
    return OutputMessage(
        role=role,
        parts=[Text(content=content)],
        finish_reason=finish_reason or "stop",
    )


def _clean_attrs(attrs: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Remove None values from GenAI attributes."""
    if not attrs:
        return {}
    return {key: value for key, value in attrs.items() if value is not None}


def _set_current_span_attrs(attrs: Optional[dict[str, Any]]) -> None:
    """
    Add custom attributes to the currently active GenAI span.

    The 0.1.14 handler context-manager API does not accept arbitrary attributes
    on workflow/agent/tool helpers, so custom NicheDocBot context is attached to
    the active span after entering the context.
    """
    clean_attrs = _clean_attrs(attrs)
    if not clean_attrs:
        return

    span = trace.get_current_span()
    if span and span.is_recording():
        for key, value in clean_attrs.items():
            span.set_attribute(key, value)


@contextmanager
def workflow_invocation(
    *,
    conversation_id: Optional[str] = None,
    input_text: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any | None]:
    """
    Represent one complete NicheDocBot workflow.

    Concrete meaning:
    One user/simulator request entering NicheDocBot and moving through the
    agent workflow.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    with handler.workflow(name=GENAI_WORKFLOW_NAME) as workflow:
        _set_current_span_attrs({
            "conversation.id": conversation_id,
            "gen_ai.workflow.name": GENAI_WORKFLOW_NAME,
            "workflow.name": GENAI_WORKFLOW_NAME,
            "workflow.input_present": bool(input_text),
            **_clean_attrs(attributes),
        })
        yield workflow


@contextmanager
def agent_invocation(
    *,
    conversation_id: Optional[str] = None,
    input_text: Optional[str] = None,
    model: Optional[str] = None,
    tools: Optional[list[str]] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any | None]:
    """
    Represent one NicheDocBot agent execution.

    Concrete meaning:
    The NicheDocBot LangGraph agent handling one request.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    with handler.invoke_local_agent(
        provider=GENAI_SYSTEM,
        request_model=model,
    ) as agent:
        _set_current_span_attrs({
            "conversation.id": conversation_id,
            "gen_ai.agent.name": GENAI_AGENT_NAME,
            "agent.name": GENAI_AGENT_NAME,
            "agent.type": "documentation_rag_agent",
            "agent.tools": ",".join(tools or []),
            "agent.input_present": bool(input_text),
            **_clean_attrs(attributes),
        })
        yield agent


@contextmanager
def step_invocation(
    *,
    name: str,
    step_type: Optional[str] = None,
    objective: Optional[str] = None,
    status: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any | None]:
    """
    Represent a NicheDocBot workflow step.

    Runtime note:
    The installed Docker API does not expose a native Step object, so this helper
    records the step as attributes on the current active span. It intentionally
    does not pretend that a Splunk StepInvocation exists.
    """
    _set_current_span_attrs({
        "workflow.phase": name,
        "workflow.step.type": step_type,
        "workflow.step.objective": objective,
        "workflow.step.status": status,
        **_clean_attrs(attributes),
    })
    yield None


@contextmanager
def retrieval_invocation(
    *,
    query: str,
    top_k: Optional[int] = None,
    documents_retrieved: Optional[int] = None,
    results: Optional[list[dict[str, Any]]] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any | None]:
    """
    Represent a vector retrieval operation.

    Runtime note:
    The installed Docker API does not expose RetrievalInvocation. Until a native
    retrieval helper is available, represent Chroma/vector retrieval as a tool
    invocation named chroma_vector_retrieval with retrieval-specific attributes.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    tool_args = {
        "query": query,
        "top_k": top_k,
    }

    with handler.tool(
        name="chroma_vector_retrieval",
        arguments=_clean_attrs(tool_args),
        tool_type="retrieval",
        tool_description="Chroma vector-store retrieval for RAG context",
    ) as retrieval:
        _set_current_span_attrs({
            "retrieval.query_present": bool(query),
            "retrieval.top_k": top_k,
            "retrieval.documents_retrieved": documents_retrieved,
            "retrieval.results_count": len(results or []),
            **_clean_attrs(attributes),
        })

        if retrieval is not None:
            retrieval.tool_result = {
                "documents_retrieved": documents_retrieved,
                "results_count": len(results or []),
            }

        yield retrieval


@contextmanager
def llm_chat_invocation(
    *,
    request_model: str,
    input_text: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any | None]:
    """
    Represent one chat LLM invocation.

    Concrete meaning:
    One actual Ollama chat/model call.

    Runtime note:
    The installed Docker API exposes handler.inference(...) as the public
    context-manager API for LLM-like inference calls. Token counts and output
    messages can be attached by the caller before the context exits.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()

    with handler.inference(
        provider=GENAI_SYSTEM,
        request_model=request_model,
        server_address=OLLAMA_SERVER_ADDRESS,
        server_port=OLLAMA_SERVER_PORT,
    ) as llm:
        _set_current_span_attrs({
            "gen_ai.operation.name": "chat",
            "gen_ai.system": GENAI_SYSTEM,
            "gen_ai.request.model": request_model,
            **_clean_attrs(attributes),
        })

        if llm is not None and input_text:
            try:
                llm.input_messages.append(text_input("user", input_text))
            except AttributeError:
                # Keep smoke/integration tests resilient if the runtime API changes.
                pass

        yield llm


@contextmanager
def tool_call_invocation(
    *,
    name: str,
    arguments: Optional[dict[str, Any]] = None,
    tool_type: Optional[str] = None,
    tool_description: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any | None]:
    """
    Represent an external tool call.

    Concrete meaning:
    A tool/service operation used by the agent, such as GitHub repository
    search or GitHub commit lookup.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    with handler.tool(
        name=name,
        arguments=arguments or {},
        tool_type=tool_type,
        tool_description=tool_description,
    ) as tool_call:
        _set_current_span_attrs(_clean_attrs(attributes))
        yield tool_call
