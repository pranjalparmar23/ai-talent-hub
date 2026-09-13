from unittest.mock import patch
from app.rag.retriever import RAGRetriever


def test_retrieve_returns_list():
    retriever = RAGRetriever()
    mock_docs = [{"content": "Docker tutorial", "metadata": {}}]
    with patch.object(retriever, "retrieve", return_value=mock_docs):
        result = retriever.retrieve("Docker basics", "learning_resources")
        assert result == mock_docs
