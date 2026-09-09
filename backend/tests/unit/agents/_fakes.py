"""Shared test fakes for agent unit tests.

Why fakes and not mocks: LangChain's RunnableSequence is a Pydantic BaseModel,
which forbids attribute assignment. Trying to patch `chain.ainvoke` or set
`agent.chain.ainvoke = mock` both fail. The clean workaround: replace
`agent.chain` wholesale with a small fake class that mimics the interface.

Every agent's `parse()` / `analyze()` / `generate()` method calls
`await self.chain.ainvoke({...})` and reads `.content` off the response —
that's the entire contract this fake honors.
"""


class FakeMsg:
    """Stand-in for LangChain's AIMessage. Only exposes `.content`."""

    def __init__(self, content: str):
        self.content = content


class FakeChain:
    """Replaces `agent.chain` wholesale in unit tests.

    Usage:
        agent.chain = FakeChain(response=FakeMsg('{"score": 85}'))
        # or to simulate an LLM failure:
        agent.chain = FakeChain(exc=Exception("Groq rate limit"))

        # After the test runs:
        assert fake.calls[0]["resume"] == expected_input
    """

    def __init__(self, *, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []  # captured call args for assertions

    async def ainvoke(self, args: dict):
        self.calls.append(args)
        if self._exc is not None:
            raise self._exc
        return self._response