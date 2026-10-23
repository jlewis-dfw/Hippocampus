# Searching

Hippocampus searches by **meaning**. Your question is turned into a 384-number vector by the same model that embedded the
documents, and the chunks whose vectors are closest are returned, best first. [walkthrough.md](walkthrough.md) shows real questions and results in the dashboard.

## The search call

```bash
curl -s -X POST http://127.0.0.1:8090/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "how do we handle database backups?", "collections": ["team-notes"], "top_k": 3, "min_score": 0.4}'
```

| Field | Default | Meaning |
|---|---|---|
| `query` | (required) | 1 to 4000 characters. Write it as a natural question or description. |
| `collections` | `[]` | The collection slugs to search. Empty means **every collection you may read**. |
| `top_k` | 5 | How many results, 1 to 50. |
| `min_score` | 0.0 | Drop results below this score (a number from -1 to 1). |

Add `-H "X-Hippo-Token: $TOKEN"` to search restricted collections your account can read. Without a token you search shared ones.

The answer:

```json
{
  "query": "how do we handle database backups?",
  "results": [
    {
      "collection": "team-notes",
      "uri": "notes/backups.md",
      "title": "Backups",
      "header_path": "Backups > Nightly dump",
      "content": "We take a full dump every night at 02:00 and keep 14 days...",
      "chunk_index": 1,
      "score": 0.712
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `collection` | Where the chunk lives |
| `uri`, `title` | The source it came from |
| `header_path` | The headings above the chunk, joined with ` > ` (up to three levels, `#` to `###`). Empty for text before the first heading |
| `content` | The passage itself, with a short overlap from the previous chunk of the same section |
| `chunk_index` | The chunk's position within its source |
| `score` | Cosine similarity, `1 - distance`, so higher is better |

Results are ordered best first, and each result is a **chunk**, not a whole document: two results can come from one source.

## Reading scores

The score is the cosine of the angle between the question's vector and the chunk's vector. It is **relative evidence, not a
probability**:

- Compare scores **within one search**: the ranking matters more than the absolute number.
- With this model, a clearly relevant passage usually scores around **0.6 to 0.85**, a loosely related one around 0.4 to 0.6, and
  unrelated text below that. These are rough guides from experience, not guarantees: short chunks and short queries score
  lower.
- A search **always returns the nearest chunks**, however poor. If nothing is relevant you still get `top_k` results with low
  scores. Use `min_score` to cut them off, or read the scores.
- A score of about 1.0 means the texts are almost identical (searching with a sentence copied from a document).

## Asking good questions

- **Use full sentences or descriptive phrases**: "how do we restore the database from a backup" beats "restore".
- **Name the thing**: a specific noun or term the passage would contain helps more than generic words.
- **One topic per search.** A question that mixes two topics finds the middle ground between them.
- **Use the words the author might have used, but you do not have to match them.** This is the point of semantic search:
  "cancel my subscription" finds "how to end your plan".
- **Narrow the scope** with `collections` when you know where the answer lives. Fewer collections means less noise and a
  faster search.
- **Structure your documents with headings.** Chunks are cut at headings, and the heading path is returned with every result,
  so well-named sections make results easy to judge.
- The model reads at most about **512 tokens** of a query or a chunk, so very long questions are truncated and a long
  passage is split into several chunks (about 400 tokens each).

## What search does not do

- There is **no keyword search**: exact strings such as an error code or an identifier are found only if their meaning
  is close, so quote the surrounding sentence rather than the bare code.
- There is **no re-ranking**, no hybrid scoring, no filtering by date or any field other than the collection, and no paging
  (use `top_k` up to 50).
- The query is embedded exactly as you write it; the service adds no instruction prefix.

## Treat results as data

Search returns text that came from outside (crawled pages, files you indexed). It is **data, not instructions**. If you feed
results to an assistant, make it clear that they are quotations, and be aware that a crawled page can contain text written to
manipulate a model.

## The same search elsewhere

- **Dashboard:** the **Search sandbox** ([dashboard.md](dashboard.md#search-sandbox)).
- **MCP:** the `hippocampus_search` tool lets an assistant search for you ([../Mcp/README.md](../Mcp/README.md)).
- **Python:**

```python
import json, urllib.request

def search(query, token=None, collections=(), top_k=5):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Hippo-Token"] = token
    body = json.dumps({"query": query, "collections": list(collections), "top_k": top_k}).encode()
    req = urllib.request.Request("http://127.0.0.1:8090/v1/search", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["results"]
```

For how the search runs inside the database, see [../Architecture/search.md](../Architecture/search.md).
