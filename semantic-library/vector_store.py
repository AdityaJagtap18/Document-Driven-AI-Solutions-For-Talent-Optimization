"""Vector store implementations behind one shared interface, so swapping the
local dev backend for Azure AI Search later touches only how a store is
constructed, not how build_index.py / search.py use it."""

import json
import os
from abc import ABC, abstractmethod


def cosine_scores(vectors, query_vector):
    """vectors: a list of vectors or an (n, dim) array-like; query_vector: one
    vector. Returns a numpy array of cosine similarity scores, shape (n,).
    Shared by LocalVectorStore.search() and any ad-hoc ranking that needs the
    same scoring without going through a full VectorStore (e.g. ranking one
    resume's chunks against a JD at parse time, before they're persisted)."""
    import numpy as np

    v = np.asarray(vectors, dtype=np.float32)
    q = np.asarray(query_vector, dtype=np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    v_norms = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-10)
    return v_norms @ q_norm


class VectorStore(ABC):
    @abstractmethod
    def add(self, doc_id: str, vector: list, metadata: dict = None):
        """Adds or replaces one document's vector + metadata."""

    @abstractmethod
    def search(self, query_vector: list, top_k: int = 5) -> list:
        """Returns up to top_k [{id, score, metadata}], highest cosine similarity first."""

    @abstractmethod
    def save(self):
        """Persists the index (no-op for backends that are already durable, e.g. Azure AI Search)."""

    @abstractmethod
    def load(self):
        """Loads a previously persisted index, if one exists."""

    @abstractmethod
    def find_by_metadata(self, key: str, value) -> list:
        """Returns every [{id, vector, metadata}] whose metadata[key] == value
        — e.g. all chunks for one resume_name, regardless of similarity."""


class LocalVectorStore(VectorStore):
    """A local JSON-backed vector store using numpy cosine similarity — zero
    external services required. Fine for the hundreds-of-resumes scale this
    project is at; not meant to replace Azure AI Search at real scale."""

    def __init__(self, path: str):
        self.path = path
        self._ids = []
        self._vectors = None  # numpy array, shape (n, dim)
        self._metadata = {}
        if os.path.exists(path):
            self.load()

    def add(self, doc_id: str, vector: list, metadata: dict = None):
        import numpy as np

        v = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if self._vectors is None:
            self._vectors = v
        elif doc_id in self._ids:
            idx = self._ids.index(doc_id)
            self._vectors[idx] = v
        else:
            self._vectors = np.vstack([self._vectors, v])
        if doc_id not in self._ids:
            self._ids.append(doc_id)
        self._metadata[doc_id] = metadata or {}

    def search(self, query_vector: list, top_k: int = 5) -> list:
        import numpy as np

        if self._vectors is None or not self._ids:
            return []
        scores = cosine_scores(self._vectors, query_vector)
        top_idx = np.argsort(-scores)[:top_k]
        return [
            {"id": self._ids[i], "score": float(scores[i]), "metadata": self._metadata[self._ids[i]]}
            for i in top_idx
        ]

    def save(self):
        data = {
            "ids": self._ids,
            "vectors": self._vectors.tolist() if self._vectors is not None else [],
            "metadata": self._metadata,
        }
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def load(self):
        import numpy as np

        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._ids = data["ids"]
        self._vectors = np.array(data["vectors"], dtype=np.float32) if data["vectors"] else None
        self._metadata = data["metadata"]

    def find_by_metadata(self, key: str, value) -> list:
        if self._vectors is None:
            return []
        return [
            {"id": doc_id, "vector": self._vectors[i], "metadata": self._metadata[doc_id]}
            for i, doc_id in enumerate(self._ids)
            if self._metadata[doc_id].get(key) == value
        ]

    def __len__(self):
        return len(self._ids)


class AzureAISearchVectorStore(VectorStore):
    """Target production backend — see ../semantic-library/index_schema.json
    for the index definitions this expects to exist. NOT YET TESTED: no Azure
    AI Search resource is provisioned. Requires AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_KEY, AZURE_SEARCH_INDEX in the environment."""

    def __init__(self, index_name: str = None):
        self.index_name = index_name or os.environ.get("AZURE_SEARCH_INDEX", "resume-chunks")

    def _require_env(self):
        missing = [n for n in ("AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_KEY") if not os.environ.get(n)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Add them to your .env file once Azure AI Search is provisioned."
            )

    def _client(self):
        self._require_env()
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        return SearchClient(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            index_name=self.index_name,
            credential=AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]),
        )

    def add(self, doc_id: str, vector: list, metadata: dict = None):
        doc = {"chunk_id": doc_id, "embedding": vector, **(metadata or {})}
        self._client().upload_documents(documents=[doc])

    def search(self, query_vector: list, top_k: int = 5) -> list:
        from azure.search.documents.models import VectorizedQuery

        results = self._client().search(
            search_text=None,
            vector_queries=[VectorizedQuery(vector=query_vector, k_nearest_neighbors=top_k, fields="embedding")],
            top=top_k,
        )
        return [{"id": r["chunk_id"], "score": r["@search.score"], "metadata": r} for r in results]

    def save(self):
        pass  # Azure AI Search writes are durable immediately on add(); nothing to flush.

    def load(self):
        pass  # Nothing to load locally; reads go straight through search().

    def find_by_metadata(self, key: str, value) -> list:
        results = self._client().search(search_text=None, filter=f"{key} eq '{value}'", top=1000)
        return [{"id": r["chunk_id"], "vector": r.get("embedding"), "metadata": r} for r in results]
