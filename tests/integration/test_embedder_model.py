import os

import pytest

from hippocampus.core.embedder import DIMENSIONS

pytestmark = pytest.mark.skipif(
    not os.environ.get("HIPPO_RUN_MODEL_TESTS"), reason="downloads the model; set HIPPO_RUN_MODEL_TESTS=1"
)


def test_fastembed_384_dim_cpu():
    from hippocampus.core.embedder import FastEmbedder

    vecs = FastEmbedder().embed(["hello world", "second text"])
    assert len(vecs) == 2 and all(len(v) == DIMENSIONS for v in vecs)
