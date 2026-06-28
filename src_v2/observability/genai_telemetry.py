"""
Splunk / OpenTelemetry GenAI telemetry helpers for NicheDocBot.

This module centralizes the Splunk GenAI utility usage so graph nodes do not
need to know the low-level handler API directly.

Important design rule:
- Use Splunk GenAI objects for real GenAI operations:
  Workflow, AgentInvocation, Step, RetrievalInvocation, LLMInvocation, ToolCall.
- Keep NicheDocBot-specific state as attributes, not as fake LLM/retrieval metrics.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from opentelemetry.util.genai.handler import get_telemetry_handler
from opentelemetry.util.genai.types import (
    AgentInvocation,
    Error,
    InputMessage,
    LLMInvocation,
    OutputMessage,
    RetrievalInvocation,
    Step,
    Text,
    ToolCall,
    Workflow,
)


GENAI_WORKFLOW_NAME = "nichedocbot.repo_rag_answer"
GENAI_AGENT_NAME = "NicheDocBot"
GENAI_FRAMEWORK = "langgraph"
GENAI_SYSTEM = "ollama"


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
    return OutputMessage(role=role, parts=[Text(content=content)], finish_reason=finish_reason)


def _clean_attrs(attrs: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    Remove None values from GenAI attributes.

    The GenAI utility accepts an attributes dictionary. We keep custom
    attributes explicit and avoid sending empty dimensions.
    """
    if not attrs:
        return {}
    return {key: value for key, value in attrs.items() if value is not None}


@contextmanager
def workflow_invocation(
    *,
    conversation_id: Optional[str] = None,
    input_text: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Workflow | None]:
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
    workflow = Workflow(
        name=GENAI_WORKFLOW_NAME,
        workflow_type="rag_agent",
        framework=GENAI_FRAMEWORK,
        system=GENAI_SYSTEM,
        conversation_id=conversation_id,
        input_messages=[text_input("user", input_text)] if input_text else [],
        attributes=_clean_attrs(attributes),
    )

    workflow = handler.start_workflow(workflow)
    try:
        yield workflow
    except Exception as exc:
        handler.fail_workflow(workflow, Error(message=str(exc), type=type(exc)))
        raise
    else:
        handler.stop_workflow(workflow)


@contextmanager
def agent_invocation(
    *,
    conversation_id: Optional[str] = None,
    input_text: Optional[str] = None,
    model: Optional[str] = None,
    tools: Optional[list[str]] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[AgentInvocation | None]:
    """
    Represent one NicheDocBot agent execution.

    Concrete meaning:
    The NicheDocBot LangGraph agent handling one request.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    agent = AgentInvocation(
        name=GENAI_AGENT_NAME,
        agent_type="documentation_rag_agent",
        framework=GENAI_FRAMEWORK,
        system=GENAI_SYSTEM,
        conversation_id=conversation_id,
        model=model,
        tools=tools or [],
        input_messages=[text_input("user", input_text)] if input_text else [],
        attributes=_clean_attrs(attributes),
    )

    agent = handler.start_agent(agent)
    try:
        yield agent
    except Exception as exc:
        handler.fail_agent(agent, Error(message=str(exc), type=type(exc)))
        raise
    else:
        handler.stop_agent(agent)


@contextmanager
def step_invocation(
    *,
    name: str,
    step_type: Optional[str] = None,
    objective: Optional[str] = None,
    status: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Step | None]:
    """
    Represent a NicheDocBot workflow step.

    Concrete meaning:
    A named phase of the agent workflow such as retrieval, human approval,
    repo readiness, ingestion, or final answer.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    step = Step(
        name=name,
        step_type=step_type,
        objective=objective,
        status=status,
        source="agent",
        framework=GENAI_FRAMEWORK,
        system=GENAI_SYSTEM,
        attributes=_clean_attrs(attributes),
    )

    step = handler.start_step(step)
    try:
        yield step
    except Exception as exc:
        handler.fail_step(step, Error(message=str(exc), type=type(exc)))
        raise
    else:
        handler.stop_step(step)


@contextmanager
def retrieval_invocation(
    *,
    query: str,
    top_k: Optional[int] = None,
    documents_retrieved: Optional[int] = None,
    results: Optional[list[dict[str, Any]]] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[RetrievalInvocation | None]:
    """
    Represent a vector retrieval operation.

    Concrete meaning:
    A Chroma/vector-store search for documents or chunks relevant to the
    user's question. This is retrieval telemetry, not LLM telemetry.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    retrieval = RetrievalInvocation(
        operation_name="retrieval",
        retriever_type="vector_store",
        query=query,
        top_k=top_k,
        documents_retrieved=documents_retrieved,
        results=results or [],
        server_address="local-chroma",
        attributes=_clean_attrs(attributes),
    )

    retrieval = handler.start_retrieval(retrieval)
    try:
        yield retrieval
    except Exception as exc:
        handler.fail_retrieval(retrieval, Error(message=str(exc), type=type(exc)))
        raise
    else:
        handler.stop_retrieval(retrieval)


@contextmanager
def llm_chat_invocation(
    *,
    request_model: str,
    input_text: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[LLMInvocation | None]:
    """
    Represent one chat LLM invocation.

    Concrete meaning:
    One actual Ollama chat/model call. Token counts and output messages should
    be attached by the caller before the context exits.
    """
    if not genai_enabled():
        yield None
        return

    handler = get_telemetry_handler()
    llm = LLMInvocation(
        request_model=request_model,
        operation="chat",
        server_address="host.docker.internal",
        server_port=11434,
        system=GENAI_SYSTEM,
        input_messages=[text_input("user", input_text)] if input_text else [],
        attributes=_clean_attrs(attributes),
    )

    llm = handler.start_llm(llm)
    try:
        yield llm
    except Exception as exc:
        handler.fail_llm(llm, Error(message=str(exc), type=type(exc)))
        raise
    else:
        handler.stop_llm(llm)


@contextmanager
def tool_call_invocation(
    *,
    name: str,
    arguments: Optional[dict[str, Any]] = None,
    tool_type: Optional[str] = None,
    tool_description: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[ToolCall | None]:
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
    tool_call = ToolCall(
        name=name,
        arguments=arguments or {},
        tool_type=tool_type,
        tool_description=tool_description,
        framework=GENAI_FRAMEWORK,
        system=GENAI_SYSTEM,
        attributes=_clean_attrs(attributes),
    )

    tool_call = handler.start_tool_call(tool_call)
    try:
        yield tool_call
    except Exception as exc:
        handler.fail_tool_call(tool_call, Error(message=str(exc), type=type(exc)))
        raise
    else:
        handler.stop_tool_call(tool_call)
