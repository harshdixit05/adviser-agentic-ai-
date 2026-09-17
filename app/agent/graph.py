"""
The advisor, as a LangGraph state machine.

One agent, one tool loop, one gate:

    understand → act ⇄ tools → verify → END
                        │          │
                        │       compose      (only on a failed check)
                        └──────────┘

`act` writes the draft itself once it stops calling tools, so an ordinary turn
costs one drafting call rather than two. `compose` runs only to rewrite a draft
that verification rejected.

`understand` keeps the learner profile current across turns. `act` is the only
node that may call tools. `compose` drafts. `verify` is deterministic Python
that checks the draft against what the tools returned, which is what makes the
no-invention rule a property of the system rather than a request to the model.
"""

from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from app.agent.nodes.verify import verify_node
from app.agent.state import AgentState, LearnerProfile
from app.agent.tools.langchain_tools import ALL_TOOLS, TOOLS_BY_NAME

MAX_TOOL_LOOPS = 4

SYSTEM_PROMPT = """You are the Intellimindz Foundation learning advisor.

You help people find FinTech courses that fit their background, goal and time.

Rules you cannot break:
- State course facts only from tool results. Never recall a course, price,
  duration or module from memory.
- Prices are indicative bands. Quote a range as a range. Never average a range
  into one figure.
- Intellimindz publishes no prerequisites. Call a sequence a suggested order,
  never a requirement.
- If a tool returns nothing or reports a field as not published, say so plainly
  and offer to connect them to the team. Do not fill the gap.
- Ask at most one clarifying question at a time, and only when it changes what
  you would recommend.

Be brief and concrete. Name courses by their exact catalogue title."""

PROFILE_PROMPT = """Extract what this message reveals about the learner.

Return JSON with these keys, using null for anything not stated:
  background, career_goal, current_level, audience, domain_interest,
  hours_available

current_level must be one of: discovery, fluency, beginner, intermediate,
advanced, or null.
audience should match an Intellimindz persona where the message implies one,
such as "Chartered Accountants", "Banking Professionals", "Government
Employees", "NGOs", "School Teachers".
hours_available is a number of hours, or null.

Infer nothing that is not there. Message:
{message}"""


def _last_human(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def build_graph(model: BaseChatModel):
    """
    `model` is injected so the graph can be exercised with a stub in tests and
    with Gemini in production.
    """
    model_with_tools = model.bind_tools(ALL_TOOLS)

    # -- understand --------------------------------------------------------

    def understand(state: AgentState) -> dict:
        message = _last_human(state)
        profile = state.get("profile") or LearnerProfile()
        if not message:
            return {"profile": profile}

        try:
            reply = model.invoke(
                [HumanMessage(content=PROFILE_PROMPT.format(message=message))]
            )
            payload = str(reply.content).strip().removeprefix("```json").removeprefix("```")
            payload = payload.removesuffix("```").strip()
            update = LearnerProfile(**json.loads(payload))
        except Exception:
            # Extraction is an optimisation, never a gate: a turn still works
            # with whatever profile we already had.
            return {"profile": profile}

        return {"profile": profile.merge(update)}

    # -- act ---------------------------------------------------------------

    def act(state: AgentState) -> dict:
        profile = state.get("profile") or LearnerProfile()
        context = ""
        if not profile.is_empty():
            known = {k: v for k, v in profile.model_dump(mode="json").items() if v is not None}
            context = f"\n\nWhat you know about this learner so far: {json.dumps(known)}"

        conversation = [SystemMessage(content=SYSTEM_PROMPT + context)]
        conversation += state.get("messages", [])
        reply = model_with_tools.invoke(conversation)

        # When the model stops calling tools, what it just wrote is the draft.
        # Re-generating it in `compose` would spend a second call on the same
        # answer, so compose is reserved for rewrites after a failed check.
        if getattr(reply, "tool_calls", None):
            return {"messages": [reply]}
        return {"messages": [reply], "draft": str(reply.content)}

    def run_tools(state: AgentState) -> dict:
        last = state["messages"][-1]
        outputs: list[ToolMessage] = []
        collected = list(state.get("tool_results", []))

        for call in getattr(last, "tool_calls", []) or []:
            tool = TOOLS_BY_NAME.get(call["name"])
            if tool is None:
                result = {"error": f"unknown tool {call['name']}"}
            else:
                try:
                    result = tool.invoke(call.get("args", {}))
                except Exception as error:  # a broken tool must not fabricate
                    result = {"error": f"{type(error).__name__}: {error}"}

            collected.append({"tool": call["name"], "results": result})
            outputs.append(
                ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )

        return {"messages": outputs, "tool_results": collected}

    def route_after_act(state: AgentState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            tool_turns = sum(1 for m in state["messages"] if isinstance(m, ToolMessage))
            if tool_turns < MAX_TOOL_LOOPS:
                return "tools"
        return "verify"

    # -- compose -----------------------------------------------------------

    def compose(state: AgentState) -> dict:
        unsupported = state.get("unsupported_claims") or []

        instruction = (
            "Write the reply now, using only the tool results above."
            if not unsupported
            else (
                "Your previous draft claimed things the catalogue does not "
                f"support: {'; '.join(unsupported)}. Rewrite using only values "
                "present in the tool results. If a figure is not there, say it "
                "is not published rather than estimating."
            )
        )

        conversation = [SystemMessage(content=SYSTEM_PROMPT)]
        conversation += state.get("messages", [])
        conversation.append(HumanMessage(content=instruction))

        reply = model.invoke(conversation)
        return {"draft": str(reply.content)}

    def route_after_verify(state: AgentState) -> str:
        return "done" if state.get("answer") else "recompose"

    def finalise(state: AgentState) -> dict:
        return {"messages": [AIMessage(content=state.get("answer", ""))]}

    # -- assembly ----------------------------------------------------------

    graph = StateGraph(AgentState)
    graph.add_node("understand", understand)
    graph.add_node("act", act)
    graph.add_node("tools", run_tools)
    graph.add_node("compose", compose)
    graph.add_node("verify", verify_node)
    graph.add_node("finalise", finalise)

    graph.set_entry_point("understand")
    graph.add_edge("understand", "act")
    graph.add_conditional_edges("act", route_after_act, {"tools": "tools", "verify": "verify"})
    graph.add_edge("tools", "act")
    graph.add_edge("compose", "verify")
    graph.add_conditional_edges(
        "verify", route_after_verify, {"done": "finalise", "recompose": "compose"}
    )
    graph.add_edge("finalise", END)

    return graph.compile()
