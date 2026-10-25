# Search

How `POST /v1/search` runs: what is embedded, the SQL, how scores and filters interact, and what to know about the vector index.
Source: `api.py` (the route) and `store.py` (`PgStore.search`).

## Steps

1. **Validate.** `query` 1 to 4000 characters, `top_k` 1 to 50, `min_score` -1 to 1; each named slug must be well formed.
2. **Embed the query** with the same embedder as the documents, one text, no prefix.
3. **Decide the scope.** Named collections must each be readable by the caller (`404` otherwise). With no names, the scope is every
   collection the caller may read. See [../Api/authorization.md](../Api/authorization.md).
4. **Run the vector search** over exactly that list of slugs.
5. **Drop results below `min_score`** and return the rest.

## The query

```sql
SELECT k.slug, s.uri, s.title, c.header_path, c.content, c.chunk_index,
       1 - (c.embedding <=> %(v)s::vector) AS score
FROM hippocampus.document_chunks c
JOIN hippocampus.sources s ON s.id = c.source_id
JOIN hippocampus.collections k ON k.id = s.collection_id
WHERE k.slug = ANY(%(cols)s::text[])
ORDER BY c.embedding <=> %(v)s::vector
LIMIT %(n)s
```

- `<=>` is pgvector's **cosine distance**; `score = 1 - distance` is the cosine similarity, so higher is better and identical directions
  score 1. Unrelated passages score low (see [../User_Guide/searching.md](../User_Guide/searching.md#reading-scores) for practical ranges).
- The vector is passed as a literal (`[0.1,0.2,...]`) and cast to `vector`.
- `store.search(embedding, collections, top_k, min_score)` has **no wildcard**: an empty `collections` list returns an empty result, so
  forgetting to pass the readable set can only ever show less, never more.

### Filtering happens before the limit

The collection filter is part of the same query as `ORDER BY ... LIMIT`, so the database ranks only chunks the caller may see. The
alternative, searching everything and discarding hidden rows afterwards, would let hidden chunks use up the `top_k` slots (returning fewer
visible results than asked) and would be a leak by timing and counts. Tests cover this with restricted chunks that out-rank every visible
chunk ([../Testing/README.md](../Testing/README.md)).

### `min_score` is applied after the limit

`min_score` is a plain filter on the returned rows (in `store.search`, after the query). A search for `top_k = 5` with a high `min_score`
can therefore return fewer than five results, and never reaches past the nearest `top_k` to find ones that qualify.

## The vector index

The schema creates an **HNSW** index on the embedding column with the cosine operator class:

```sql
CREATE INDEX idx_document_chunks_embedding_hnsw ON hippocampus.document_chunks
USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
```

What this means in practice (general pgvector behaviour; this project does not tune it):

- HNSW is an **approximate** nearest-neighbour index: it trades a small chance of missing the true best match for speed.
- When a query also has a `WHERE` filter, the planner may scan the index and filter afterwards. A very selective filter on a large table
  can then return fewer rows than `LIMIT`. At the scale this service targets (thousands to tens of thousands of chunks) the planner
  will usually choose an exact scan, which is precise and fast enough (not measured here). If you ever hold millions of chunks and search narrow
  collections, look at pgvector's `hnsw.ef_search` and iterative-scan settings, or partition by collection.
- The index is built on insert; large imports are slower because of it.
- Distance must use the same operator as the index (`<=>`); a different operator would ignore the index.

## Cost

One search = one embedding of the query on the CPU (the first call after a start also loads the model) plus one SQL query. Results carry the chunk text, so the response size is `top_k` times a few kilobytes.

## What is deliberately not here

No keyword or hybrid search, no re-ranking model, no date or tag filters, no query expansion, no caching of query vectors, no paging.
Adding a lexical component would need a text-search column and index, and a rule for combining scores.

## Tests

`tests/unit/test_api.py` (ranking, scoped and unscoped search, `min_score`, `top_k`), `tests/unit/test_access.py` (hidden collections never appear,
named hidden collections are 404), and `tests/integration/test_store_integration.py` (the filter runs before the limit, against real pgvector).
