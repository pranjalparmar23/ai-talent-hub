"""Tests for evals.langsmith_upload, with a fake LangSmith client. No network, no key.

`make_client` (the real client) is only tested for its missing-package message.
"""

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import langsmith_upload as up
from evals.langsmith_upload import (
    Result,
    UploadError,
    build_example,
    dataset_name,
    describe,
    differs,
    main,
    sync,
    version_of,
)
from tests.unit.evals._builders import (
    retrieval_case,
    retrieval_dict,
    roadmap_dict,
    write_dataset,
)

ENV = {"LANGSMITH_API_KEY": "key"}


class FakeClient:
    """Keeps examples per dataset, like LangSmith does."""

    def __init__(self):
        self.datasets: dict[str, SimpleNamespace] = {}
        self.store: dict[uuid.UUID, dict[uuid.UUID, SimpleNamespace]] = {}
        self.calls: list[tuple] = []

    def has_dataset(self, *, dataset_name):
        return dataset_name in self.datasets

    def create_dataset(self, name, *, description, metadata):
        dataset = SimpleNamespace(
            id=uuid.uuid4(), name=name, description=description, metadata=metadata
        )
        self.datasets[name] = dataset
        self.store[dataset.id] = {}
        self.calls.append(("create_dataset", name))
        return dataset

    def read_dataset(self, *, dataset_name):
        return self.datasets[dataset_name]

    def list_examples(self, *, dataset_id):
        return list(self.store[dataset_id].values())

    def create_examples(self, *, dataset_id, inputs, outputs, metadata, splits):
        self.calls.append(("create_examples", len(inputs)))
        for i, o, m, s in zip(inputs, outputs, metadata, splits):
            example = SimpleNamespace(
                id=uuid.uuid4(),
                inputs=i,
                outputs=o,
                metadata={**m, "dataset_split": [s]},  # LangSmith adds its own keys
            )
            self.store[dataset_id][example.id] = example

    def update_example(self, example_id, *, inputs, outputs, metadata, split):
        self.calls.append(("update_example", example_id))
        for examples in self.store.values():
            if example_id in examples:
                examples[example_id] = SimpleNamespace(
                    id=example_id,
                    inputs=inputs,
                    outputs=outputs,
                    metadata={**metadata, "dataset_split": [split]},
                )

    def examples_of(self, name):
        return self.store[self.datasets[name].id]

    def only(self, name):
        return list(self.examples_of(name).values())


def examples_for(*cases):
    return [build_example(c) for c in cases]


def do_sync(client, examples, name="ai-talent-hub-retrieval-v1"):
    return sync(client, name, examples, description="d", metadata={"area": "retrieval"})


# ── names ────────────────────────────────────────────────────


def test_dataset_name():
    assert dataset_name("retrieval", 1) == "ai-talent-hub-retrieval-v1"
    assert dataset_name("roadmap", 12) == "ai-talent-hub-roadmap-v12"


def test_version_of():
    assert version_of(Path("data/links.v3.jsonl")) == 3
    assert version_of(Path("retrieval.v10.jsonl")) == 10


@pytest.mark.parametrize(
    "name", ["links.jsonl", "links.v1.baseline.json", "links.vX.jsonl"]
)
def test_version_of_rejects_names_without_a_version(name):
    with pytest.raises(UploadError, match="cannot read a version"):
        version_of(Path(name))


# ── one example ──────────────────────────────────────────────


def test_build_example_maps_a_case():
    case = retrieval_case(
        "ret-001", tags=("technical", "clear"), split="test", role_family="technical"
    )
    example = build_example(case)
    assert example["case_id"] == "ret-001"
    assert example["split"] == "test"
    assert example["inputs"] == {
        "query": "dynamic programming",
        "collection": "interview_knowledge",
        "role_family": "technical",
        "top_k": 5,
    }
    assert example["outputs"] == {
        "expect_empty": False,
        "relevant_keywords": ["dynamic programming"],
        "relevant_source_ids": [],
        "relevant_domains": [],
    }
    assert example["metadata"] == {
        "case_id": "ret-001",
        "area": "retrieval",
        "tags": ["technical", "clear"],
        "split": "test",
        "source": "hand-written",
        "added": "2026-11-23",
        "notes": "A core topic that the guide covers.",
    }


def test_build_example_leaves_out_empty_input_fields_and_a_missing_pair():
    example = build_example(retrieval_case())
    assert "domain" not in example["inputs"] and "level" not in example["inputs"]
    assert "pair_with" not in example["metadata"]


def test_build_example_keeps_pair_with_when_there_is_one():
    case = retrieval_case(tags=("technical", "fairness"), pair_with="ret-002")
    assert build_example(case)["metadata"]["pair_with"] == "ret-002"


def test_build_example_is_plain_json():
    json.dumps(build_example(retrieval_case()))  # `added` became text, not a date


# ── comparing with what LangSmith holds ──────────────────────


def remote_of(example, **changes):
    fields = {
        "inputs": example["inputs"],
        "outputs": example["outputs"],
        "metadata": {**example["metadata"], "dataset_split": ["dev"]},
    }
    fields.update(changes)
    return SimpleNamespace(id=uuid.uuid4(), **fields)


def test_an_identical_example_does_not_differ_even_with_extra_metadata():
    (example,) = examples_for(retrieval_case())
    assert differs(example, remote_of(example)) is False


@pytest.mark.parametrize(
    "change",
    [
        {"inputs": {"query": "other"}},
        {"outputs": {"expect_empty": True}},
        {"metadata": {"case_id": "ret-001", "split": "test"}},
    ],
)
def test_a_changed_part_differs(change):
    (example,) = examples_for(retrieval_case())
    assert differs(example, remote_of(example, **change)) is True


def test_a_missing_metadata_key_differs():
    (example,) = examples_for(retrieval_case())
    remote = remote_of(example, metadata={"case_id": "ret-001"})
    assert differs(example, remote) is True


def test_none_inputs_and_outputs_count_as_empty():
    (example,) = examples_for(retrieval_case())
    remote = remote_of(example, inputs=None, outputs=None, metadata=None)
    assert differs(example, remote) is True  # they are not the case's values


# ── syncing a dataset ────────────────────────────────────────


def test_a_new_dataset_is_created_with_every_example():
    client = FakeClient()
    cases = [retrieval_case("ret-001"), retrieval_case("ret-002")]
    result = do_sync(client, examples_for(*cases))
    assert result.created == ["ret-001", "ret-002"]
    assert result.updated == result.unchanged == result.extra == []
    assert client.calls == [
        ("create_dataset", "ai-talent-hub-retrieval-v1"),
        ("create_examples", 2),
    ]
    stored = client.only("ai-talent-hub-retrieval-v1")
    assert {e.metadata["case_id"] for e in stored} == {"ret-001", "ret-002"}
    assert {e.metadata["dataset_split"][0] for e in stored} == {"dev"}


def test_the_dataset_gets_a_description_and_metadata():
    client = FakeClient()
    sync(client, "n", [], description="the description", metadata={"area": "x"})
    dataset = client.datasets["n"]
    assert dataset.description == "the description" and dataset.metadata == {
        "area": "x"
    }


def test_running_again_changes_nothing():
    client = FakeClient()
    examples = examples_for(retrieval_case("ret-001"), retrieval_case("ret-002"))
    do_sync(client, examples)
    client.calls.clear()
    result = do_sync(client, examples)
    assert result.unchanged == ["ret-001", "ret-002"]
    assert result.created == result.updated == []
    assert client.calls == []  # not even an empty create call


def test_only_the_missing_examples_are_created():
    client = FakeClient()
    do_sync(client, examples_for(retrieval_case("ret-001")))
    client.calls.clear()
    result = do_sync(
        client, examples_for(retrieval_case("ret-001"), retrieval_case("ret-002"))
    )
    assert result.created == ["ret-002"] and result.unchanged == ["ret-001"]
    assert client.calls == [("create_examples", 1)]


def test_a_changed_label_overwrites_the_example_and_nothing_is_duplicated():
    client = FakeClient()
    do_sync(client, examples_for(retrieval_case("ret-001")))
    changed = retrieval_case("ret-001", expected={"relevant_keywords": ["graphs"]})
    result = do_sync(client, examples_for(changed))
    assert result.updated == ["ret-001"] and result.created == []
    (stored,) = client.only("ai-talent-hub-retrieval-v1")
    assert stored.outputs["relevant_keywords"] == ["graphs"]


def test_an_example_that_is_not_in_the_file_is_reported_and_kept():
    client = FakeClient()
    do_sync(client, examples_for(retrieval_case("ret-001"), retrieval_case("ret-002")))
    result = do_sync(client, examples_for(retrieval_case("ret-001")))
    assert result.extra == ["ret-002"]
    assert len(client.only("ai-talent-hub-retrieval-v1")) == 2  # never deleted
    assert not any(call[0].startswith("delete") for call in client.calls)


def test_an_example_added_by_hand_without_a_case_id_is_ignored():
    client = FakeClient()
    do_sync(client, [])
    dataset = client.datasets["ai-talent-hub-retrieval-v1"]
    client.store[dataset.id][uuid.uuid4()] = SimpleNamespace(
        id=uuid.uuid4(), inputs={}, outputs={}, metadata={"note": "by hand"}
    )
    result = do_sync(client, examples_for(retrieval_case("ret-001")))
    assert result.extra == [] and result.created == ["ret-001"]


def test_a_case_id_seen_twice_in_langsmith_is_reported():
    client = FakeClient()
    (example,) = examples_for(retrieval_case("ret-001"))
    do_sync(client, [example])
    dataset = client.datasets["ai-talent-hub-retrieval-v1"]
    client.store[dataset.id][uuid.uuid4()] = remote_of(example)
    result = do_sync(client, [example])
    assert result.duplicated == ["ret-001"]
    assert result.unchanged == ["ret-001"]


def test_two_datasets_do_not_share_examples():
    client = FakeClient()
    do_sync(client, examples_for(retrieval_case("ret-001")), name="one")
    result = do_sync(client, examples_for(retrieval_case("ret-001")), name="two")
    assert result.created == ["ret-001"]


# ── the printed summary ──────────────────────────────────────


def test_describe_a_plain_result():
    result = Result("ds", created=["a", "b"], unchanged=["c"])
    assert describe(result, verbose=False) == "ds: 2 created, 0 updated, 1 unchanged"


def test_describe_verbose_lists_each_change():
    result = Result("ds", created=["a"], updated=["b"])
    text = describe(result, verbose=True)
    assert "  created a" in text and "  updated b" in text


def test_describe_warns_about_overwrites_extras_and_duplicates():
    result = Result("ds", updated=["a"], extra=["x", "y"], duplicated=["z"])
    text = describe(result, verbose=False)
    assert "differed from the file and were overwritten" in text
    assert "in LangSmith but not in the file (left alone): x, y" in text
    assert "more than one example for the same case id: z" in text


# ── main ─────────────────────────────────────────────────────


@pytest.fixture
def datasets(tmp_path, monkeypatch):
    write_dataset(
        tmp_path,
        monkeypatch,
        "retrieval",
        [retrieval_dict("ret-001"), retrieval_dict("ret-002", split="test")],
    )
    write_dataset(tmp_path, monkeypatch, "roadmap", [roadmap_dict("rdm-001")])
    return tmp_path


def run(argv, client=None, environ=ENV, factory=None):
    return main(argv, client_factory=factory or (lambda: client), environ=environ)


def test_dry_run_needs_no_key_and_no_client(datasets, capsys):
    def never():
        raise AssertionError("a client was made")

    code = run(["--dry-run"], environ={}, factory=never)
    out = capsys.readouterr().out
    assert code == 0
    assert (
        "ai-talent-hub-retrieval-v1: would send 2 examples from retrieval.v1.jsonl (dev 1, test 1)"
        in out
    )
    assert (
        "ai-talent-hub-roadmap-v1: would send 1 examples from links.v1.jsonl (dev 1, test 0)"
        in out
    )


def test_without_a_key_nothing_is_uploaded_and_it_is_not_an_error(datasets, capsys):
    def never():
        raise AssertionError("a client was made")

    code = run([], environ={}, factory=never)
    assert code == 0
    assert (
        "LANGSMITH_API_KEY is not set, so nothing was uploaded."
        in capsys.readouterr().out
    )


def test_an_empty_key_counts_as_no_key(datasets, capsys):
    assert run([], environ={"LANGSMITH_API_KEY": ""}) == 0
    assert "not set" in capsys.readouterr().out


def test_a_run_with_a_key_uploads_both_areas(datasets, capsys):
    client = FakeClient()
    code = run([], client)
    out = capsys.readouterr().out
    assert code == 0
    assert "ai-talent-hub-retrieval-v1: 2 created, 0 updated, 0 unchanged" in out
    assert "ai-talent-hub-roadmap-v1: 1 created, 0 updated, 0 unchanged" in out
    assert set(client.datasets) == {
        "ai-talent-hub-retrieval-v1",
        "ai-talent-hub-roadmap-v1",
    }


def test_one_area_only(datasets, capsys):
    client = FakeClient()
    run(["--area", "roadmap"], client)
    assert set(client.datasets) == {"ai-talent-hub-roadmap-v1"}


def test_the_dataset_description_names_the_file_and_the_source_of_truth(datasets):
    client = FakeClient()
    run(["--area", "retrieval"], client)
    dataset = client.datasets["ai-talent-hub-retrieval-v1"]
    assert "retrieval.v1.jsonl" in dataset.description
    assert "source of truth" in dataset.description
    assert dataset.metadata == {"area": "retrieval", "file": "retrieval.v1.jsonl"}


def test_a_second_run_is_all_unchanged(datasets, capsys):
    client = FakeClient()
    run([], client)
    capsys.readouterr()
    run([], client)
    out = capsys.readouterr().out
    assert "0 created, 0 updated, 2 unchanged" in out


def test_verbose_lists_the_cases_created(datasets, capsys):
    run(["--verbose", "--area", "retrieval"], FakeClient())
    assert "  created ret-001" in capsys.readouterr().out


def test_a_broken_dataset_stops_everything_before_anything_is_sent(
    datasets, monkeypatch, capsys
):
    path = datasets / "roadmap" / "links.v1.jsonl"
    path.write_text(json.dumps(roadmap_dict(split="x")) + "\n", encoding="utf-8")
    client = FakeClient()
    code = run([], client)
    err = capsys.readouterr().err
    assert code == 1
    assert "roadmap: PROBLEMS" in err and "links.v1.jsonl:1" in err
    assert client.calls == []  # retrieval was fine, but nothing was uploaded


def test_a_missing_version_exits_one(datasets, capsys):
    assert run(["--version", "9"], FakeClient()) == 1
    assert "no v9" in capsys.readouterr().err


def test_a_langsmith_error_exits_one_and_says_a_rerun_is_safe(datasets, capsys):
    class Broken(FakeClient):
        def has_dataset(self, **kwargs):
            raise RuntimeError("403 forbidden")

    code = run([], Broken())
    err = capsys.readouterr().err
    assert code == 1
    assert "Upload failed: RuntimeError: 403 forbidden" in err
    assert "Running it again is safe." in err


def test_an_upload_error_from_the_factory_exits_one(datasets, capsys):
    def factory():
        raise UploadError("the langsmith package is not installed")

    assert run([], factory=factory) == 1
    assert (
        "Upload failed: the langsmith package is not installed"
        in capsys.readouterr().err
    )


def test_make_client_says_what_to_install_when_the_package_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "langsmith", None)  # makes `import langsmith` fail
    with pytest.raises(UploadError, match="pip install langsmith"):
        up.make_client()


def test_a_bad_argument_exits_two(datasets):
    with pytest.raises(SystemExit) as stopped:
        run(["--area", "ats"], FakeClient())
    assert stopped.value.code == 2


def test_the_key_name_is_the_one_the_documentation_gives():
    assert up.KEY_NAME == "LANGSMITH_API_KEY"
