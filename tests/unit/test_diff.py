from hippocampus.core.diff import Document, sync_collection


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return [[0.0] * 384 for _ in texts]


class FakeStore:
    def __init__(self):
        self.sources = {}  # uri -> (sha, chunks)

    def source_hashes(self, collection):
        return {u: s for u, (s, _) in self.sources.items()}

    def replace_source(self, collection, uri, title, sha256, chunks):
        self.sources[uri] = (sha256, chunks)

    def delete_source(self, collection, uri):
        del self.sources[uri]


def docs(**kw):
    return {u: Document(None, t) for u, t in kw.items()}


def test_unchanged_skipped_changed_reembedded_deleted_purged():
    store, emb = FakeStore(), FakeEmbedder()
    r = sync_collection("c", docs(a="# A\none", b="# B\ntwo"), store, emb)
    assert sorted(r.added) == ["a", "b"] and not r.changed
    calls = emb.calls

    r = sync_collection("c", docs(a="# A\none", b="# B\nTWO changed"), store, emb)
    assert r.unchanged == ["a"] and r.changed == ["b"]
    assert emb.calls == calls + 1  # only b re-embedded

    r = sync_collection("c", docs(a="# A\none"), store, emb)
    assert r.deleted == ["b"] and "b" not in store.sources


def test_embed_failure_leaves_old_vectors():
    store = FakeStore()
    sync_collection("c", docs(a="# A\none"), store, FakeEmbedder())
    old = store.sources["a"]

    class Boom:
        def embed(self, texts):
            raise RuntimeError("down")

    try:
        sync_collection("c", docs(a="# A\nnew"), store, Boom())
    except RuntimeError:
        pass
    assert store.sources["a"] == old


def test_prune_false_keeps_missing():
    store = FakeStore()
    sync_collection("c", docs(a="x", b="y"), store, FakeEmbedder())
    r = sync_collection("c", docs(a="x"), store, FakeEmbedder(), prune=False)
    assert r.deleted == [] and "b" in store.sources
