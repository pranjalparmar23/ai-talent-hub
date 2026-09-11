import pytest
from unittest.mock import patch, AsyncMock
from app.rag.retriever import RAGRetriever


@pytest.mark.asyncio
async def test_retrieve_returns_list():
    retriever = RAGRetriever()
    mock_docs = [{"content": "Docker tutorial", "metadata": {}}]
    with patch.object(retriever, "retrieve", return_value=mock_docs):
        result = await retriever.retrieve("Docker basics", "learning_resources")
    assert isinstance(result, list)
    assert len(result) > 0
