"""Demo MCP server for Talent Hub development.

A tiny, self-hosted MCP server (streamable HTTP) with one read-only tool. It exists so
the MCPGateway can be exercised end to end — real network, real protocol — without any
third-party service, key or terms of service. The notes below are original text written
for this project.

Run locally:   python server.py          (listens on 0.0.0.0:8100, path /mcp)
In Docker:     see the `mcp-demo` service in docker/docker-compose.yml
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

NOTES = [
    {
        "topic": "behavioral",
        "title": "Structure a behavioral answer",
        "text": "Name the situation in one sentence, say what you were responsible "
        "for, describe the actions you took, and finish with a measurable result "
        "and what you learned.",
    },
    {
        "topic": "behavioral",
        "title": "Choose stories before the interview",
        "text": "Prepare four or five stories that cover conflict, failure, "
        "leadership, a tight deadline and something you are proud of. One story "
        "can answer several questions.",
    },
    {
        "topic": "technical",
        "title": "Think aloud while solving",
        "text": "State the problem in your own words, ask about limits and edge "
        "cases, outline an approach before coding, and test with a small example.",
    },
    {
        "topic": "technical",
        "title": "Talk about trade-offs",
        "text": "When you pick a design, name one alternative and say why you did "
        "not choose it. Interviewers listen for reasoning more than the label.",
    },
    {
        "topic": "communication",
        "title": "Handle a question you cannot answer",
        "text": "Say what you do know, explain how you would find out the rest, and "
        "stay calm. A clear process is a better answer than a guess.",
    },
    {
        "topic": "hr",
        "title": "Answer 'tell me about yourself'",
        "text": "Give a short present, past, future story: what you do now, the "
        "experience that led here, and why this role is the next step.",
    },
]


def _security() -> TransportSecuritySettings:
    """Allow the Host headers this server is reached by (comma-separated env var).

    The MCP SDK rejects unknown Host headers (DNS-rebinding protection). Inside Docker
    the gateway calls us as `mcp-demo:8100`, so that name must be allowed.
    """
    hosts = os.getenv("MCP_DEMO_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,mcp-demo:*")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()],
        allowed_origins=[],
    )


mcp = FastMCP(
    "talent-hub-demo-reference",
    host=os.getenv("MCP_DEMO_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_DEMO_PORT", "8100")),
    transport_security=_security(),
)


@mcp.tool()
def search_reference_notes(query: str, limit: int = 3) -> str:
    """Search the demo interview-tips notes. Returns matching notes as plain text."""
    words = {w for w in query.lower().split() if len(w) > 2}
    limit = max(1, min(int(limit), 5))
    scored = []
    for note in NOTES:
        haystack = f"{note['topic']} {note['title']} {note['text']}".lower()
        score = sum(1 for w in words if w in haystack)
        if score:
            scored.append((score, note))
    scored.sort(key=lambda pair: -pair[0])
    if not scored:
        return "No matching notes."
    return "\n\n".join(
        f"[{n['topic']}] {n['title']}: {n['text']}" for _, n in scored[:limit]
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")