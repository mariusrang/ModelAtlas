"""A scripted LLM for the agent tests: it replays the answers we give it."""


class ScriptedLLM:
    """`chat_replies` are returned in order by chat(), `json_replies` by json().

    A chat reply is a dict {"content": ..., "tool_calls": [{"name", "arguments"}]}.
    When a list runs out, chat() stops calling tools and json() returns {}.
    """

    name = "scripted-test-llm"

    def __init__(self, chat_replies=None, json_replies=None):
        self.chat_replies = list(chat_replies or [])
        self.json_replies = list(json_replies or [])
        self.chat_calls: list[dict] = []
        self.json_calls: list[dict] = []

    def chat(self, messages, tools=None):
        self.chat_calls.append({"messages": messages, "tools": tools})
        if self.chat_replies:
            return self.chat_replies.pop(0)
        return {"content": "done", "tool_calls": []}

    def json(self, messages, schema):
        self.json_calls.append({"messages": messages, "schema": schema})
        return self.json_replies.pop(0) if self.json_replies else {}


def call(name, **arguments):
    """One planning reply that calls one tool."""
    return {"content": "", "tool_calls": [{"name": name, "arguments": arguments}]}
