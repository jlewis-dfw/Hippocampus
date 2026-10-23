# Embedding and chunking

How text becomes searchable: first it is cut into chunks (`chunker.py`), then each chunk becomes a vector (`embedder.py`).

## The embedding model

| Property | Value |
|---|---|
| Model | `BAAI/bge-small-en-v1.5` |
| Vector size | 384 numbers (`DIMENSIONS`), the same width as the `vector(384)` column |
| Runtime | FastEmbed on ONNX Runtime, `CPUExecutionProvider` only; it never touches a GPU |
| Loading | Lazy: the model loads on the first `embed` call, then stays in memory |
| Language | English-trained; it works on other languages with lower quality |
| Input limit | About 512 tokens; longer text is truncated by the model, which is why chunks are kept near 400 tokens |

`FastEmbedder.embed(texts)` returns one list of floats per text and **raises** if a vector does not have exactly 384 numbers. An empty
input list returns an empty list without loading the model. Where the model is stored is set by `HIPPO_MODEL_CACHE` (the image
bakes it into `/opt/model-cache` at build time, so the container needs no internet at run time).

`Embedder` is a `Protocol` with one method, `embed(texts) -> list[list[float]]`. The tests substitute fakes (a keyword-based
vector function), so almost nothing in the test suite loads the real model.

Documents and queries are embedded with the **same call and no prefix**. (Some retrieval models recommend an instruction prefix
for queries; this service does not add one.)

### Why a fixed model

The vector width is part of the schema, and vectors from different models are not comparable. Switching models means a new
column definition and re-embedding every chunk, so the model is a decision of the build, not a setting. An alternative
provider (for example a local model server's embedding endpoint) is possible behind the `Embedder` protocol, but a collection must
only ever use one model.

## Chunking

`chunk_markdown(text, target_tokens=400, max_code_tokens=512, overlap_ratio=0.12)` returns a list of `Chunk(index, header_path,
content, token_count)`.

### Algorithm

1. **Sections by heading.** The text is scanned line by line. A line `#`, `##` or `###` (one to three hashes, then text; trailing
   hashes are stripped) starts a new section. Headings deeper than `###` do **not** split: they stay in the body. A heading stack
   tracks the path, so `## Install` after `# Guide` gives the section path `Guide > Install`, and a later `# Other` pops back.
2. **Blocks inside a section.** Prose lines are grouped into paragraphs (split at blank lines). A **fenced code block** (three or
   more backticks or tildes) is one block, and headings inside a fence are ignored. An unterminated fence is kept as code to the end.
3. **Sizing.** Token counts use a cheap estimate: the number of words and punctuation marks (`approx_tokens`), which undercounts the
   model's real tokenizer slightly. A paragraph larger than the target is split by words. A code block over `max_code_tokens` is split
   by lines, each piece re-wrapped in the same fence so every chunk is valid Markdown.
4. **Packing.** Blocks of one section are packed greedily into chunks up to the target size. A chunk never spans two sections.
5. **Overlap.** Each chunk after the first in a section starts with a copy of the last words of the previous chunk, about 12% of that
   chunk's size (at least one word, at most the length of its final prose block), so an idea cut at a boundary appears in both. No overlap
   is added when the previous chunk ends in a code block.
6. Chunks get consecutive indexes across the whole document.

`overlap_ratio` must be in `[0, 0.5)`; anything else raises `ValueError`. Empty sections are dropped. Text before the first heading
becomes a chunk with an empty `header_path`.

### Parameters and their effect

| Parameter | Default | Raising it | Lowering it |
|---|---|---|---|
| `target_tokens` | 400 | fewer, broader chunks; more context per hit; blurrier matches | more, narrower chunks; sharper matches; more storage |
| `max_code_tokens` | 512 | keeps larger code blocks whole | splits code earlier |
| `overlap_ratio` | 0.12 | more repeated text, fewer boundary misses | less repetition |

These are function defaults, not settings; the pipeline always uses the defaults. Change them in code and re-index.

### What this means for your documents

- **Headings are the structure.** Put each topic under a heading of its own; the heading path is returned with every hit.
- **Keep sections focused.** A very long section is cut into several chunks that share a heading path.
- **Use `#` to `###`.** Deeper headings help a human reader but do not create chunk boundaries.
- **Tables and lists** are ordinary text lines and are packed like prose.

## Token counts

Each chunk stores its `token_count` (the estimate). It is informational (it is returned nowhere by the API) and is used by the packer.

## Tests

`tests/unit/test_chunker.py` covers heading paths, fences, oversized prose and code, overlap, empty input and unterminated fences.
`tests/integration/test_embedder_model.py` runs the real model and is skipped unless `HIPPO_RUN_MODEL_TESTS=1`.
