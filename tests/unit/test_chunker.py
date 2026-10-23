from hippocampus.chunker import approx_tokens, chunk_markdown

DOC = """# Top
intro text

## Sub
sub text

### Deep
deep text

#### Four
four stays in body

## Sibling
sibling text
"""


def test_header_paths_nested():
    chunks = chunk_markdown(DOC)
    assert [c.header_path for c in chunks] == [
        "Top", "Top > Sub", "Top > Sub > Deep", "Top > Sibling",
    ]
    assert "#### Four" not in chunks[2].content or "four stays in body" in chunks[2].content
    assert [c.index for c in chunks] == [0, 1, 2, 3]


def test_headers_inside_fences_ignored():
    doc = "# A\n```\n# not a header\nx = 1\n```\nafter\n"
    chunks = chunk_markdown(doc)
    assert len(chunks) == 1
    assert "# not a header" in chunks[0].content


def test_small_code_block_atomic():
    code = "\n".join(f"line{i}" for i in range(50))
    doc = f"# A\n```py\n{code}\n```\n"
    chunks = chunk_markdown(doc, target_tokens=20, max_code_tokens=512)
    assert len(chunks) == 1 and f"```py\n{code}\n```" in chunks[0].content


def test_large_code_block_split_and_rewrapped():
    code = "\n".join(f"line{i} = {i}" for i in range(400))
    doc = f"# A\n```py\n{code}\n```\n"
    chunks = chunk_markdown(doc, target_tokens=400, max_code_tokens=100)
    assert len(chunks) > 1
    for c in chunks:
        assert c.content.startswith("```py") and c.content.rstrip().endswith("```")
    assert all(approx_tokens(c.content) <= 400 for c in chunks)
    assert all(c.content.count("```") % 2 == 0 for c in chunks)  # fences balanced


def test_overlap_between_prose_chunks_of_one_section():
    paras = [" ".join(f"p{p}w{i}" for i in range(30)) for p in range(6)]
    doc = "# A\n" + "\n\n".join(paras)
    chunks = chunk_markdown(doc, target_tokens=60, overlap_ratio=0.12)
    assert len(chunks) > 1
    prev_words = chunks[0].content.split()
    assert chunks[1].content.split()[0] in prev_words  # starts with carried-over tail
    assert all(c.header_path == "A" for c in chunks)


def test_no_overlap_after_code_or_across_sections():
    doc = "# A\ntext one\n\n# B\ntext two\n"
    chunks = chunk_markdown(doc)
    assert chunks[1].content == "text two"


def test_zero_overlap():
    paras = [" ".join(f"w{p}_{i}" for i in range(30)) for p in range(4)]
    chunks = chunk_markdown("# A\n" + "\n\n".join(paras), target_tokens=60, overlap_ratio=0)
    joined = "\n\n".join(c.content for c in chunks)
    assert joined == "\n\n".join(paras)


def test_unterminated_fence_and_empty_input():
    assert chunk_markdown("") == []
    assert len(chunk_markdown("# A\n```\nopen forever\n")) == 1
