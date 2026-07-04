# ruff: noqa
import os
import re
import json
import datetime
from typing import AsyncGenerator, Any
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, ToolContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp.client.stdio import StdioServerParameters
from google.adk.workflow import Workflow, START, node, FunctionNode
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.genai import types

from .config import config

# ----------------------------------------------------------------------
# MCP Server Toolsets Configuration
# ----------------------------------------------------------------------

mcp_server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")

# Create Toolsets with filtered tools for specialized agents
medication_mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", mcp_server_script],
        )
    ),
    tool_filter=["add_medication", "get_medications", "log_wellbeing"],
)

# Shared Visit MCP Toolset
visit_mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", mcp_server_script],
        )
    ),
    tool_filter=["schedule_appointment", "get_appointments"],
)

# ----------------------------------------------------------------------
# Callback for Caregiver Approval Gate
# ----------------------------------------------------------------------

async def mcp_tool_approval_callback(tool: Any, args: dict, tool_context: ToolContext, tool_response: dict) -> dict | None:
    """Intercepts scheduling actions and flags caregiver approval requirement."""
    tool_name = tool.name
    if tool_name == "add_medication":
        tool_context.state["caregiver_needs_approval"] = True
        tool_context.state["pending_action"] = f"Add medication schedule: {args.get('name')} ({args.get('dosage')}) at {args.get('time_of_day')}"
    elif tool_name == "schedule_appointment":
        tool_context.state["caregiver_needs_approval"] = True
        tool_context.state["pending_action"] = f"Schedule appointment with Dr. {args.get('doctor')} on {args.get('datetime_str')} for {args.get('reason')}"
    return None

# ----------------------------------------------------------------------
# Specialized LlmAgents (Sub-agents)
# ----------------------------------------------------------------------

medication_agent = LlmAgent(
    name="medication_agent",
    model=config.model,
    instruction="""You are a specialized medication tracking assistant.
You handle medication scheduling, dosage tracking, and logging intake events.
Use the mcp tools to log schedules or changes.
Ensure all details (name, dosage, time_of_day) are provided. If any details are missing, ask the user.
Always be extremely clear and precise about drug dosages.""",
    tools=[medication_mcp_toolset],
    after_tool_callback=mcp_tool_approval_callback,
)

visit_agent = LlmAgent(
    name="visit_agent",
    model=config.model,
    instruction="""You are a specialized doctor visit and appointment coordinator.
You schedule medical appointments and log summaries from doctor visits.
Use the mcp tools to schedule appointments.
Ensure all details (doctor, datetime_str, reason) are provided. If any are missing, ask the user.
Always be professional and helpful.""",
    tools=[visit_mcp_toolset],
    after_tool_callback=mcp_tool_approval_callback,
)

# ----------------------------------------------------------------------
# Orchestrator (Lead Coordinator)
# ----------------------------------------------------------------------

medication_tool = AgentTool(agent=medication_agent)
visit_tool = AgentTool(agent=visit_agent)

orchestrator = LlmAgent(
    name="orchestrator",
    model=config.model,
    instruction="""You are the lead coordinator for the Elderly Care Assistant.
Analyze the user's request:
1. If the request is about medication schedules, intake, or dosages, delegate to the medication_agent tool.
2. If the request is about doctor visits, scheduling, or appointments, delegate to the visit_agent tool.
3. If the request is general, answer it directly.
Keep responses polite, caring, and clear. Avoid diagnosing medical issues yourself.""",
    tools=[medication_tool, visit_tool],
)

# ----------------------------------------------------------------------
# Security Checkpoint Configuration & Logic
# ----------------------------------------------------------------------

SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_REGEX = re.compile(r"\b(?:\+?1[-.●]?)?\(?([0-9]{3})\)?[-.●]?([0-9]{3})[-.●]?([0-9]{4})\b")
INSURANCE_REGEX = re.compile(r"\bMEDICARE-\d{4}-\d{4}\b", re.IGNORECASE)

INJECTION_KEYWORDS = [
    "system prompt",
    "override safety",
    "ignore previous instructions",
    "you are now",
    "dan mode",
    "ignore rules",
    "bypass"
]

def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    text_content = ""
    if node_input and node_input.parts:
        text_content = "".join([p.text for p in node_input.parts if p.text])
        
    scrubbed_text = text_content
    pii_detected = False
    
    # 1. PII Scrubbing
    if SSN_REGEX.search(scrubbed_text):
        scrubbed_text = SSN_REGEX.sub("[REDACTED_SSN]", scrubbed_text)
        pii_detected = True
    if PHONE_REGEX.search(scrubbed_text):
        scrubbed_text = PHONE_REGEX.sub("[REDACTED_PHONE]", scrubbed_text)
        pii_detected = True
    if INSURANCE_REGEX.search(scrubbed_text):
        scrubbed_text = INSURANCE_REGEX.sub("[REDACTED_INSURANCE_ID]", scrubbed_text)
        pii_detected = True

    # 2. Prompt Injection Detection
    injection_detected = False
    lowered_content = text_content.lower()
    for kw in INJECTION_KEYWORDS:
        if kw in lowered_content:
            injection_detected = True
            break

    # 3. Domain-Specific Rule: Consent check for sharing medical records externally
    domain_rule_triggered = False
    sharing_keywords = ["share", "email", "send", "export", "transmit"]
    consent_keywords = ["consent", "permission", "authorized", "approved by caregiver"]
    
    has_sharing = any(skw in lowered_content for skw in sharing_keywords)
    has_consent = any(ckw in lowered_content for ckw in consent_keywords)
    
    if has_sharing and not has_consent:
        domain_rule_triggered = True

    # Decisions and Audit logging
    if injection_detected:
        severity = "CRITICAL"
        decision = "unsafe"
        message = "Access Blocked: Prompt injection attempt detected."
    elif domain_rule_triggered:
        severity = "WARNING"
        decision = "unsafe"
        message = "Access Blocked: Caregiver consent required to share medical/medication information externally."
    else:
        severity = "WARNING" if pii_detected else "INFO"
        decision = "safe"
        message = "Input verified as safe."

    # Update input to scrubbed version if safe
    if decision == "safe" and pii_detected:
        new_parts = [types.Part.from_text(text=scrubbed_text)]
        node_input = types.Content(role=node_input.role, parts=new_parts)

    audit_log = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": "security_checkpoint_audit",
        "severity": severity,
        "pii_detected": pii_detected,
        "injection_detected": injection_detected,
        "domain_rule_triggered": domain_rule_triggered,
        "decision": decision,
        "message": message
    }
    print(json.dumps(audit_log))
    
    ctx.state["last_security_audit"] = audit_log

    if decision == "unsafe":
        return Event(output=message, route="unsafe")
    return Event(output=node_input, route="safe")

def security_failure(node_input: str) -> Event:
    return Event(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=f"⚠️ Access Blocked: {node_input}")]
        )
    )

# ----------------------------------------------------------------------
# Helper Routing & Final Nodes
# ----------------------------------------------------------------------

def check_approval_required(ctx: Context, node_input: Any) -> Event:
    if ctx.state.get("caregiver_needs_approval", False):
        return Event(output=node_input, route="needs_approval")
    return Event(output=node_input, route="no_approval")

async def human_approval_node(ctx: Context, node_input: Any) -> AsyncGenerator[Event, None]:
    if not ctx.resume_inputs or "caregiver_approval" not in ctx.resume_inputs:
        pending_action = ctx.state.get("pending_action", "requested action")
        yield RequestInput(
            interrupt_id="caregiver_approval",
            message=f"✋ Caregiver Approval Required for: {pending_action}. Do you approve? (yes/no):"
        )
        return
        
    decision = ctx.resume_inputs["caregiver_approval"].strip().lower()
    ctx.state["caregiver_needs_approval"] = False
    
    if "yes" in decision or "approve" in decision:
        ctx.state["caregiver_decision"] = "approved"
        yield Event(output="Approved", state={"caregiver_decision": "approved"})
    else:
        ctx.state["caregiver_decision"] = "denied"
        yield Event(output="Denied", state={"caregiver_decision": "denied"})

def final_response_node(ctx: Context, node_input: Any) -> Event:
    decision = ctx.state.get("caregiver_decision")
    pending_action = ctx.state.get("pending_action")
    
    if decision == "approved":
        response_text = f"✅ Caregiver approved: {pending_action}. The action has been recorded."
        # Reset state
        ctx.state["caregiver_decision"] = None
        ctx.state["pending_action"] = None
    elif decision == "denied":
        response_text = f"❌ Caregiver denied: {pending_action}. The action was not recorded."
        # Reset state
        ctx.state["caregiver_decision"] = None
        ctx.state["pending_action"] = None
    else:
        # Simple agent output
        if isinstance(node_input, types.Content):
            response_text = "".join([p.text for p in node_input.parts if p.text])
        else:
            response_text = str(node_input)
            
    return Event(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=response_text)]
        )
    )

# ----------------------------------------------------------------------
# Workflow Definition
# ----------------------------------------------------------------------

workflow = Workflow(
    name="elderly_care_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"unsafe": security_failure, "safe": orchestrator}),
        (orchestrator, check_approval_required),
        (check_approval_required, {"needs_approval": human_approval_node, "no_approval": final_response_node}),
        (human_approval_node, final_response_node),
    ],
)

# Exposed App
app = App(
    name="app",
    root_agent=workflow,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
