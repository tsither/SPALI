from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import chromadb
import numpy as np
import voyageai

_voyage_client = None


def _get_voyage_client():
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client()  # reads VOYAGE_API_KEY from env
    return _voyage_client

# === SCHEMAS ===


@dataclass
class ModuleDocument:
    """
    Schema for course documents in ChromaDB.

    Note: Chroma metadata must be flat (no lists). For fields like 'module' that can have
    multiple values, we store them as comma-separated strings for filtering.
    """

    id: str  # Unique identifier (e.g., "fm1", "ml101")
    title: str  # Course title
    type: Literal["mandatory", "elective", "bridge"]  # Course type for filtering
    document: str  # Text to embed (e.g., "Machine Learning alias: ML")
    module: str  # Comma-separated module codes (e.g., "am11,am12")
    sem: str  # Semester offered: "w", "s", or "w,s"

    def to_metadata(self) -> dict:
        """Convert to flat metadata dict for Chroma (excludes id and document)."""
        meta = {
            "title": self.title,
            "type": self.type,
            "module": self.module,
            "sem": self.sem,
        }

        return meta


@dataclass
class CourseDocument:
    """
    Schema for course documents in ChromaDB.

    Note: Chroma metadata must be flat (no lists). For fields like 'module' that can have
    multiple values, we store them as comma-separated strings for filtering.
    """

    id: str  # Unique identifier (e.g., "fm1", "ml101")
    title: str  # Course title
    type: Literal["mandatory", "elective", "bridge"]  # Course type for filtering
    document: str  # Text to embed (e.g., "Machine Learning alias: ML")
    module: str  # Comma-separated module codes (e.g., "am11,am12")
    module_type: str  # Module type: "fm", "bm", "am", etc.
    semester: str  # Semester planned for: "wise2526", "sose26", etc.

    def to_metadata(self) -> dict:
        """Convert to flat metadata dict for Chroma (excludes id and document)."""
        meta = {
            "title": self.title,
            "type": self.type,
            "module": self.module,
            "semester": self.semester,
        }

        return meta


# === CLIENT SETUP ===

# Single DB location for all collections
CHROMA_PATH = Path(__file__).parent
client = chromadb.PersistentClient(path=str(CHROMA_PATH))
# print(CHROMA_PATH)


def get_collection(collection: str):
    """Get or create a collection for a study program."""
    return client.get_or_create_collection(
        name=collection, metadata={"hnsw:space": "cosine"}
    )


def add_courses(
    collection: str, courses: list[CourseDocument], embeddings: list[list[float]]
):
    """
    Add courses to a collection.

    courses: list of CourseDocument objects
    embeddings: list of embedding vectors (same order as courses)
    """
    collection = get_collection(collection)

    collection.add(
        ids=[c.id for c in courses],
        embeddings=embeddings,
        metadatas=[c.to_metadata() for c in courses],
        documents=[c.document for c in courses],
    )


def upsert_courses(
    collection: str, courses: list[CourseDocument], embeddings: list[list[float]]
):
    """
    Upsert courses into a collection (insert or update by id).

    courses: list of CourseDocument objects
    embeddings: list of embedding vectors (same order as courses)
    """
    collection = get_collection(collection)

    collection.upsert(
        ids=[c.id for c in courses],
        embeddings=embeddings,
        metadatas=[c.to_metadata() for c in courses],
        documents=[c.document for c in courses],
    )


def add_modules(
    collection: str, modules: list[ModuleDocument], embeddings: list[list[float]]
):
    """
    Add modules to a collection.

    modules: list of ModuleDocument objects
    embeddings: list of embedding vectors (same order as modules)
    """
    collection = get_collection(collection)

    collection.add(
        ids=[m.id for m in modules],
        embeddings=embeddings,
        metadatas=[m.to_metadata() for m in modules],
        documents=[m.document for m in modules],
    )


def _cosine_search(
    query_vec: list[float], collection, top_k: int, where_clause
) -> list[dict]:
    """Exact nearest-neighbour search via brute-force cosine similarity."""
    results = collection.get(
        where=where_clause,
        include=["embeddings", "metadatas", "documents"],
    )
    if not results["ids"]:
        return []

    vecs = np.array(results["embeddings"], dtype=np.float32)
    q = np.array(query_vec, dtype=np.float32)

    row_norms = np.linalg.norm(vecs, axis=1)
    q_norm = np.linalg.norm(q)
    row_norms = np.where(row_norms == 0, 1e-10, row_norms)
    if q_norm == 0:
        q_norm = 1e-10

    sims = (vecs / row_norms[:, None]) @ (q / q_norm)
    distances = 1.0 - sims

    top_indices = np.argsort(distances)[:top_k]
    return [
        {
            "id": results["ids"][i],
            "distance": float(distances[i]),
            "metadata": results["metadatas"][i],
            "document": results["documents"][i],
        }
        for i in top_indices
    ]


def search_courses(
    query: str, collection: str, top_k: int = 1, filters: dict = None, embed_fn=None
):
    """
    Search courses in a chromadb collection.

    query: text query to search for
    filters: optional dict like {"type": "elective"} or {"type": "mandatory", "semester": "wise2526"}
    embed_fn: embedding function (defaults to Voyage AI if not provided)
    """
    if embed_fn is None:
        query_vec = _get_voyage_client().embed([query], model="voyage-3-large").embeddings[0]
    else:
        query_vec = embed_fn([query])[0]

    col = get_collection(collection)
    return _cosine_search(query_vec, col, top_k, filters if filters else None)


def get_items(collection: str, module_code: str = None, filters: dict = None) -> list:
    """
    Get items from a collection with optional filtering.

    module_code: optional exact match on module field (case-insensitive)
    filters: optional dict like {"type": "elective"} or {"type": "mandatory", "sem": "w"}

    Returns list of matching results.
    """
    collection = get_collection(collection)

    where_clause = None
    if module_code:
        where_clause = {"module": {"$eq": module_code.lower()}}
        if filters:
            where_clause = {"$and": [where_clause, filters]}
    elif filters:
        where_clause = filters

    results = collection.get(where=where_clause, include=["metadatas", "documents"])

    if not results["ids"]:
        return []

    return [
        {"id": id, "metadata": meta, "document": doc}
        for id, meta, doc in zip(
            results["ids"], results["metadatas"], results["documents"]
        )
    ]


def module_exists(collection: str, module_code: str) -> bool:
    """Exact-match existence check for a module code (case-insensitive), no embedding call."""
    return bool(get_items(collection, module_code=module_code))


def search_modules(
    collection: str, query: str, top_k: int = 1, filters: dict = None, embed_fn=None
):
    """
    Search modules in a chromadb collection.

    query: text query to search for
    filters: optional dict like {"type": "elective"} or {"type": "mandatory", "sem": "w"}
    embed_fn: embedding function (defaults to Voyage AI if not provided)
    """
    if embed_fn is None:
        query_vec = _get_voyage_client().embed([query], model="voyage-3-large").embeddings[0]
    else:
        query_vec = embed_fn([query])[0]

    col = get_collection(collection)
    return _cosine_search(query_vec, col, top_k, filters if filters else None)


def delete_collection(collection: str):
    """Delete a chromadb collection (useful for rebuilding)."""
    try:
        client.delete_collection(collection)
    except ValueError:
        pass  # Collection doesn't exist


# === HELPERS FOR BUILDING FROM EXISTING DATA ===


def course_from_dict(
    data: dict, course_type: Literal["mandatory", "elective", "bridge"]
) -> CourseDocument:
    """
    Convert your existing course dict format to CourseDocument.

    Input format: {"embedding": "...", "module": ["am11", "am12"], "title": "...", "sem": ["w"]}
    """
    return CourseDocument(
        id=data["module"][0],  # Use first module as ID
        title=data["title"],
        type=course_type,
        document=data["embedding"],
        module=",".join(data["module"]),
        semester=",".join(data.get("sem", ["w", "s"])),
    )


def module_from_dict(
    data: dict, module_type: Literal["mandatory", "elective", "bridge"]
) -> ModuleDocument:
    """
    Convert your existing module dict format to ModuleDocument.

    Input format: {"embedding": "...", "module": ["am11", "am12"], "title": "...", "sem": ["w"]}
    """
    return ModuleDocument(
        id=data["module"][0],  # Use first module as ID
        title=data["title"],
        type=module_type,
        document=data["embedding"],
        module=",".join(data["module"]),
        sem=",".join(data.get("sem", ["w", "s"])),
    )


def update_module_credits(collection_name: str, credits_map: dict):
    """
    Update all modules in a collection with credits based on module type.

    Derives module type from module code (e.g., 'am11' -> 'am' -> 6 credits).
    """
    collection = get_collection(collection_name)

    # Get all items
    results = collection.get(include=["metadatas"])

    if not results["ids"]:
        print("No modules found in collection")
        return

    ids_to_update = []
    metadatas_to_update = []

    for id, metadata in zip(results["ids"], results["metadatas"]):
        # Derive module type from id (e.g., 'am11' -> 'am')
        module_type = "".join(c for c in id if c.isalpha()).lower()
        credits = credits_map.get(module_type, 0)

        # Add credits to metadata
        updated_metadata = {**metadata, "credits": credits}
        ids_to_update.append(id)
        metadatas_to_update.append(updated_metadata)

    # Batch update
    collection.update(ids=ids_to_update, metadatas=metadatas_to_update)

    print(f"Updated {len(ids_to_update)} modules with credits")


def get_module_credits(collection_name: str, module_code: str) -> int:
    """Get credits for a specific module by its code."""
    collection = get_collection(collection_name)

    results = collection.get(ids=[module_code.lower()], include=["metadatas"])

    if results["metadatas"]:
        return results["metadatas"][0].get("credits", 0)
    return 0


def build_course_collection(
    collection: str,
    courses: list[dict],
    course_type: Literal["mandatory", "elective", "bridge"],
    embed_fn,
    replace: bool = False,
):
    """
    Build a Chroma collection from course data.

    Args:
        collection: Collection name (e.g., "cogsys")
        courses: List of course dicts in your existing format
        course_type: Type for all courses in this batch
        embed_fn: Function that takes list of strings, returns list of embeddings
        replace: If True, delete existing collection first
    """
    if replace:
        delete_collection(collection)

    docs = [course_from_dict(c, course_type) for c in courses]
    texts = [d.document for d in docs]
    embeddings = embed_fn(texts)

    # Convert numpy to list if needed
    if hasattr(embeddings, "tolist"):
        embeddings = embeddings.tolist()

    add_courses(collection, docs, embeddings)
    print(f"Added {len(docs)} {course_type} courses to '{collection}' collection")


def build_module_collection(
    collection: str,
    modules: list[dict],
    module_type: Literal["mandatory", "elective", "bridge"],
    embed_fn,
    replace: bool = False,
):
    """
    Build a Chroma collection from module data.

    Args:
        collection: Collection name (e.g., "cogsys")
        modules: List of module dicts in your existing format
        module_type: Type for all modules in this batch
        embed_fn: Function that takes list of strings, returns list of embeddings
        replace: If True, delete existing collection first
    """
    if replace:
        delete_collection(collection)

    docs = [module_from_dict(m, module_type) for m in modules]
    texts = [d.document for d in docs]
    embeddings = embed_fn(texts)

    # Convert numpy to list if needed
    if hasattr(embeddings, "tolist"):
        embeddings = embeddings.tolist()

    add_modules(collection, docs, embeddings)
    print(f"Added {len(docs)} {module_type} modules to '{collection}' collection")
