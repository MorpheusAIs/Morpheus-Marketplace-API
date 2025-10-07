from __future__ import annotations

import json
from typing import Any, Dict


def fix_tool_choice_structure(json_body: Dict[str, Any], logger) -> None:
    """Normalize tool_choice shape in-place, using structured logging."""

    if "tool_choice" in json_body:
        if isinstance(json_body["tool_choice"], dict) and "function" in json_body["tool_choice"]:
            func_obj = json_body["tool_choice"]["function"]
            if isinstance(func_obj, dict) and "tool_choice" in func_obj:
                logger.warning("Found nested tool_choice, fixing structure",
                              original_tool_choice=json_body["tool_choice"],
                              event_type="tool_choice_structure_fix")
                try:
                    if "name" in func_obj.get("tool_choice", {}).get("function", {}):
                        func_name = func_obj["tool_choice"]["function"]["name"]
                        fixed_tool_choice = {
                            "type": "function",
                            "function": {"name": func_name},
                        }
                        json_body["tool_choice"] = fixed_tool_choice
                        logger.info("Fixed tool_choice structure",
                                   function_name=func_name,
                                   fixed_tool_choice=fixed_tool_choice,
                                   event_type="tool_choice_fixed")
                except Exception as e:  # noqa: BLE001 - behavior-parity
                    logger.error("Error fixing tool_choice",
                               error=str(e),
                               event_type="tool_choice_fix_error")


def remove_tool_choice_from_tools(json_body: Dict[str, Any], logger) -> None:
    """Remove tool_choice nested within tools parameters in-place, using structured logging."""

    if "tools" in json_body:
        for i, tool in enumerate(json_body["tools"]):
            if isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                if isinstance(func, dict) and "parameters" in func:
                    params = func["parameters"]
                    if isinstance(params, dict) and "tool_choice" in params:
                        tool_name = func.get('name', f'tool_{i}')
                        logger.warning("Found tool_choice in tool parameters, removing",
                                     tool_index=i,
                                     tool_name=tool_name,
                                     nested_tool_choice=params['tool_choice'],
                                     event_type="nested_tool_choice_found")
                        del json_body["tools"][i]["function"]["parameters"]["tool_choice"]
                        logger.info("Removed tool_choice from tool parameters",
                                   tool_index=i,
                                   tool_name=tool_name,
                                   event_type="nested_tool_choice_removed")


def normalize_assistant_tool_call_messages(json_body: Dict[str, Any], logger) -> None:
    """Set assistant message content to None when tool_calls present and content is empty."""

    if "messages" in json_body:
        for i, msg in enumerate(json_body["messages"]):
            if (
                isinstance(msg, dict)
                and msg.get("role") == "assistant"
                and "tool_calls" in msg
            ):
                if msg.get("content") == "":
                    tool_call_count = len(msg.get("tool_calls", []))
                    logger.info("Setting null content for assistant message with tool_calls",
                               message_index=i,
                               tool_call_count=tool_call_count,
                               event_type="assistant_message_normalized")
                    json_body["messages"][i]["content"] = None


def log_tool_request_details(json_body: Dict[str, Any], session_id: str, logger) -> None:
    """Emit detailed logs for tool-calling requests using structured logging."""

    has_tools = "tools" in json_body
    has_tool_messages = False
    tool_message_count = 0
    
    if "messages" in json_body:
        tool_messages = [msg for msg in json_body["messages"] if isinstance(msg, dict) and msg.get("role") == "tool"]
        has_tool_messages = len(tool_messages) > 0
        tool_message_count = len(tool_messages)

    if has_tools or has_tool_messages:
        tool_count = len(json_body.get("tools", [])) if has_tools else 0
        message_count = len(json_body.get("messages", []))
        
        logger.info("Tool calling request detected",
                   session_id=session_id,
                   has_tools=has_tools,
                   tool_count=tool_count,
                   has_tool_messages=has_tool_messages,
                   tool_message_count=tool_message_count,
                   total_message_count=message_count,
                   request_body=json_body,
                   event_type="tool_calling_request_details")


