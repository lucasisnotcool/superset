# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Rewrite substrate: truncate/undo/fork/attempts on both conversation stores,
plus learning-loop memory provenance (plan_conversation_management_impl.md 1a)."""

from __future__ import annotations

from typing import Callable, Union

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.rewrites import build_rewrite_preview
from superset_ai_agent.conversations.schemas import (
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
)
from superset_ai_agent.conversations.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from superset_ai_agent.conversations.store import (
    ConversationMessageNotFoundError,
    ConversationRewriteError,
)
from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
from superset_ai_agent.persistence.models import AiAgentMessage
from superset_ai_agent.semantic_layer.apply_snapshots import (
    ApplySnapshotEntry,
    ApplySnapshotStore,
)
from superset_ai_agent.semantic_layer.memory_store import (
    InMemoryMemory,
    SqlAlchemyMemory,
)

AnyStore = Union[SqlAlchemyConversationStore, InMemoryConversationStore]

OWNER = "user-1"


def _sqlalchemy_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    return create_session_factory(engine)


def _sqlalchemy_store() -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(_sqlalchemy_factory())


def _scope() -> ConversationScope:
    return ConversationScope(database_id=1, schema_name="main")


STORES: list[Callable[[], AnyStore]] = [_sqlalchemy_store, InMemoryConversationStore]


def _seed_thread(store: AnyStore, turns: int = 3) -> str:
    conversation = store.create(_scope(), owner_id=OWNER)
    for index in range(turns):
        store.append(
            conversation.id,
            ConversationMessage(role="user", content=f"question {index}"),
            owner_id=OWNER,
        )
        store.append(
            conversation.id,
            ConversationMessage(
                role="assistant",
                content=f"answer {index}",
                artifacts=[ConversationArtifact(sql=f"select {index}")],
            ),
            owner_id=OWNER,
        )
    return conversation.id


@pytest.mark.parametrize("make_store", STORES)
def test_truncate_from_removes_anchor_and_tail(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store)
    thread = store.get(conversation_id, owner_id=OWNER)
    anchor = thread.messages[2]  # "question 1"
    assert anchor.role == "user"

    result = store.truncate_from(conversation_id, anchor.id, owner_id=OWNER)

    assert [m.content for m in result.conversation.messages] == [
        "question 0",
        "answer 0",
    ]
    assert [m.content for m in result.removed_messages] == [
        "question 1",
        "answer 1",
        "question 2",
        "answer 2",
    ]
    # The store reflects the truncation on a fresh read too.
    reloaded = store.get(conversation_id, owner_id=OWNER)
    assert len(reloaded.messages) == 2


@pytest.mark.parametrize("make_store", STORES)
def test_truncate_from_rejects_assistant_anchor(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store, turns=1)
    thread = store.get(conversation_id, owner_id=OWNER)
    with pytest.raises(ConversationRewriteError):
        store.truncate_from(conversation_id, thread.messages[1].id, owner_id=OWNER)


@pytest.mark.parametrize("make_store", STORES)
def test_truncate_from_rejects_unknown_message(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store, turns=1)
    with pytest.raises(ConversationMessageNotFoundError):
        store.truncate_from(conversation_id, "nope", owner_id=OWNER)


@pytest.mark.parametrize("make_store", STORES)
def test_append_after_truncate_keeps_sequences_collision_free(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store)
    thread = store.get(conversation_id, owner_id=OWNER)
    store.truncate_from(conversation_id, thread.messages[2].id, owner_id=OWNER)

    # The rewritten turn must append cleanly despite soft-deleted rows (R1).
    store.append(
        conversation_id,
        ConversationMessage(role="user", content="question 1 (edited)"),
        owner_id=OWNER,
    )
    store.append(
        conversation_id,
        ConversationMessage(role="assistant", content="answer 1 (new)"),
        owner_id=OWNER,
    )
    reloaded = store.get(conversation_id, owner_id=OWNER)
    assert [m.content for m in reloaded.messages] == [
        "question 0",
        "answer 0",
        "question 1 (edited)",
        "answer 1 (new)",
    ]


def test_sqlalchemy_sequences_stay_unique_after_truncate() -> None:
    factory = _sqlalchemy_factory()
    store = SqlAlchemyConversationStore(factory)
    conversation_id = _seed_thread(store)
    thread = store.get(conversation_id, owner_id=OWNER)
    store.truncate_from(conversation_id, thread.messages[2].id, owner_id=OWNER)
    store.append(
        conversation_id,
        ConversationMessage(role="user", content="edited"),
        owner_id=OWNER,
    )
    with factory() as session:
        sequences = [
            row.sequence
            for row in session.scalars(
                select(AiAgentMessage).where(
                    AiAgentMessage.conversation_id == conversation_id
                )
            )
        ]
    assert len(sequences) == len(set(sequences))
    assert max(sequences) == 6  # 0..5 seeded, soft-deleted rows keep theirs


@pytest.mark.parametrize("make_store", STORES)
def test_undo_truncate_restores_batch_and_drops_rewrite(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store)
    thread = store.get(conversation_id, owner_id=OWNER)
    anchor = thread.messages[2]
    store.truncate_from(conversation_id, anchor.id, owner_id=OWNER)
    store.append(
        conversation_id,
        ConversationMessage(role="user", content="question 1 (edited)"),
        owner_id=OWNER,
    )
    store.append(
        conversation_id,
        ConversationMessage(role="assistant", content="answer 1 (new)"),
        owner_id=OWNER,
    )

    restored = store.undo_truncate(conversation_id, anchor.id, owner_id=OWNER)

    assert [m.content for m in restored.messages] == [
        "question 0",
        "answer 0",
        "question 1",
        "answer 1",
        "question 2",
        "answer 2",
    ]


@pytest.mark.parametrize("make_store", STORES)
def test_undo_truncate_without_batch_raises(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store, turns=1)
    thread = store.get(conversation_id, owner_id=OWNER)
    with pytest.raises(ConversationRewriteError):
        store.undo_truncate(conversation_id, thread.messages[0].id, owner_id=OWNER)


@pytest.mark.parametrize("make_store", STORES)
def test_repeated_rewrite_of_same_anchor_does_not_resurrect_undone_turn(
    make_store,
) -> None:
    store = make_store()
    conversation_id = _seed_thread(store, turns=2)
    thread = store.get(conversation_id, owner_id=OWNER)
    anchor = thread.messages[2]
    store.truncate_from(conversation_id, anchor.id, owner_id=OWNER)
    store.append(
        conversation_id,
        ConversationMessage(role="user", content="edit A"),
        owner_id=OWNER,
    )
    store.undo_truncate(conversation_id, anchor.id, owner_id=OWNER)

    # Rewrite the same anchor again, then undo again: only the original tail
    # may come back — never the undone "edit A" turn.
    store.truncate_from(conversation_id, anchor.id, owner_id=OWNER)
    store.append(
        conversation_id,
        ConversationMessage(role="user", content="edit B"),
        owner_id=OWNER,
    )
    restored = store.undo_truncate(conversation_id, anchor.id, owner_id=OWNER)
    contents = [m.content for m in restored.messages]
    assert "edit A" not in contents
    assert "edit B" not in contents
    assert contents == ["question 0", "answer 0", "question 1", "answer 1"]


@pytest.mark.parametrize("make_store", STORES)
def test_list_attempts_returns_superseded_assistant_turns(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store, turns=2)
    thread = store.get(conversation_id, owner_id=OWNER)
    anchor = thread.messages[2]
    store.truncate_from(conversation_id, anchor.id, owner_id=OWNER)

    attempts = store.list_attempts(conversation_id, anchor.id, owner_id=OWNER)
    assert [m.content for m in attempts] == ["answer 1"]


@pytest.mark.parametrize("make_store", STORES)
def test_fork_copies_up_to_anchor_and_backlinks(make_store) -> None:
    store = make_store()
    conversation_id = _seed_thread(store)
    thread = store.get(conversation_id, owner_id=OWNER)
    anchor = thread.messages[3]  # "answer 1"

    fork = store.fork(conversation_id, anchor.id, owner_id=OWNER)

    assert fork.id != conversation_id
    assert fork.parent_conversation_id == conversation_id
    assert fork.title.endswith("(branch)")
    assert [m.content for m in fork.messages] == [
        "question 0",
        "answer 0",
        "question 1",
        "answer 1",
    ]
    # Fresh message ids: the fork must not alias the parent's rows.
    parent_ids = {m.id for m in thread.messages}
    assert all(m.id not in parent_ids for m in fork.messages)
    # Parent untouched.
    assert len(store.get(conversation_id, owner_id=OWNER).messages) == 6
    # Fork is independently appendable.
    store.append(
        fork.id,
        ConversationMessage(role="user", content="new direction"),
        owner_id=OWNER,
    )
    assert len(store.get(fork.id, owner_id=OWNER).messages) == 5


@pytest.mark.parametrize("make_store", STORES)
def test_fork_defaults_to_whole_thread_and_marks_changesets_inert(make_store) -> None:
    store = make_store()
    conversation = store.create(_scope(), owner_id=OWNER)
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="model the schema"),
        owner_id=OWNER,
    )
    store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="Proposed 2 files.",
            artifacts=[
                ConversationArtifact(
                    type="changeset",
                    payload={"items": [{"op": "create", "path": "models/a.json"}]},
                )
            ],
        ),
        owner_id=OWNER,
    )

    fork = store.fork(conversation.id, None, owner_id=OWNER)

    assert len(fork.messages) == 2
    copied = fork.messages[1].artifacts[0]
    assert copied.type == "changeset"
    assert copied.inert is True
    assert copied.payload == {"items": [{"op": "create", "path": "models/a.json"}]}
    # Source artifact stays actionable.
    source = store.get(conversation.id, owner_id=OWNER)
    assert source.messages[1].artifacts[0].inert is False


@pytest.mark.parametrize("make_store", STORES)
def test_fork_preserves_scope_and_project_pin(make_store) -> None:
    store = make_store()
    conversation = store.create(
        _scope(), owner_id=OWNER, kind="copilot", project_id="proj-1"
    )
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="hello"),
        owner_id=OWNER,
    )
    fork = store.fork(conversation.id, None, owner_id=OWNER)
    assert fork.kind == "copilot"
    assert fork.project_id == "proj-1"
    assert fork.scope.database_id == 1


def test_sqlalchemy_truncated_changeset_leaves_transcript() -> None:
    """A truncated turn's changeset artifact must vanish from live reads."""

    store = _sqlalchemy_store()
    conversation = store.create(_scope(), owner_id=OWNER)
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="model it"),
        owner_id=OWNER,
    )
    anchor = store.get(conversation.id, owner_id=OWNER).messages[0]
    store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="Proposal.",
            artifacts=[ConversationArtifact(type="changeset", payload={"items": []})],
        ),
        owner_id=OWNER,
    )
    store.truncate_from(conversation.id, anchor.id, owner_id=OWNER)
    reloaded = store.get(conversation.id, owner_id=OWNER)
    assert reloaded.messages == []


# -- Memory provenance (1a.3) -------------------------------------------------


def test_sqlalchemy_memory_records_and_deletes_by_source() -> None:
    memory = SqlAlchemyMemory(_sqlalchemy_factory())
    memory.store_confirmed(
        question="how many moves",
        semantic_sql="select 1",
        native_sql="select 1",
        database_id=1,
        source_conversation_id="conv-1",
        source_message_id="msg-1",
    )
    memory.store_confirmed(
        question="how many leads",
        semantic_sql="select 2",
        native_sql="select 2",
        database_id=1,
        source_conversation_id="conv-1",
        source_message_id="msg-2",
    )

    assert memory.delete_by_source(["msg-1"]) == 1
    remaining = memory.load_candidates(database_id=1)
    assert [pair.question for pair in remaining] == ["how many leads"]
    assert memory.delete_by_source([]) == 0


def test_sqlalchemy_memory_dedup_refresh_adopts_new_source() -> None:
    memory = SqlAlchemyMemory(_sqlalchemy_factory())
    memory.store_confirmed(
        question="how many moves",
        semantic_sql="select 1",
        native_sql="select 1",
        database_id=1,
        source_message_id="msg-old",
    )
    # Same question+sql confirmed again from a different (rewritten) turn: the
    # refreshed row must belong to the new turn so its rewrite can remove it.
    memory.store_confirmed(
        question="how many moves",
        semantic_sql="select 1",
        native_sql="select 1",
        database_id=1,
        source_message_id="msg-new",
    )
    assert memory.delete_by_source(["msg-old"]) == 0
    assert memory.delete_by_source(["msg-new"]) == 1


def test_memory_count_by_source() -> None:
    memory = SqlAlchemyMemory(_sqlalchemy_factory())
    memory.store_confirmed(
        question="q1",
        semantic_sql="s",
        native_sql="s",
        database_id=1,
        source_message_id="msg-1",
    )
    assert memory.count_by_source(["msg-1", "msg-2"]) == 1
    assert memory.count_by_source([]) == 0


def test_in_memory_memory_delete_by_source() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="q1",
        semantic_sql="s",
        native_sql="s",
        database_id=1,
        source_message_id="msg-1",
    )
    memory.store_confirmed(
        question="q2",
        semantic_sql="t",
        native_sql="t",
        database_id=1,
    )
    assert memory.delete_by_source(["msg-1"]) == 1
    assert memory.delete_by_source(["msg-1"]) == 0


# -- Revert applied drafts (Phase 2) ------------------------------------------


def _mdl_store_with_file():
    from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
    from superset_ai_agent.semantic_layer.schemas import MdlFileCreateRequest

    store = InMemoryMdlFileStore()
    created = store.create(
        "proj-1",
        MdlFileCreateRequest(
            path="models/a.json",
            content=(
                '{"models": [{"name": "a", "tableReference": {"table": "a"},'
                ' "columns": [{"name": "id", "type": "BIGINT"}]}]}'
            ),
        ),
        owner_id=OWNER,
    )
    return store, created


def _snapshot(**kwargs):
    from datetime import datetime, timezone

    from superset_ai_agent.semantic_layer.apply_snapshots import ApplySnapshot

    defaults = dict(
        apply_group_id="group-1",
        project_id="proj-1",
        conversation_id="conv-1",
        message_id="msg-applied",
        applied_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return ApplySnapshot(**defaults)


def test_revert_apply_group_restores_update_before_image() -> None:
    from superset_ai_agent.semantic_layer.copilot.service import revert_apply_group
    from superset_ai_agent.semantic_layer.schemas import MdlFileUpdateRequest

    store, created = _mdl_store_with_file()
    before = created.content
    store.update(
        created.id,
        MdlFileUpdateRequest(content='{"models": [{"name": "a"}]}'),
        owner_id=OWNER,
    )
    result = revert_apply_group(
        store,
        [
            _snapshot(
                op="update",
                path="models/a.json",
                file_id=created.id,
                before_content=before,
                before_status="draft",
            )
        ],
        apply_group_id="group-1",
        project_id="proj-1",
        owner_id=OWNER,
    )
    assert result.reverted_count == 1
    assert result.excluded == []
    assert store.get(created.id, owner_id=OWNER).content == before


def test_revert_apply_group_excludes_activated_files() -> None:
    from superset_ai_agent.semantic_layer.copilot.service import revert_apply_group
    from superset_ai_agent.semantic_layer.schemas import MdlFileUpdateRequest

    store, created = _mdl_store_with_file()
    store.update(created.id, MdlFileUpdateRequest(status="active"), owner_id=OWNER)
    result = revert_apply_group(
        store,
        [
            _snapshot(
                op="create",
                path="models/a.json",
                file_id=created.id,
            )
        ],
        apply_group_id="group-1",
        project_id="proj-1",
        owner_id=OWNER,
    )
    assert result.reverted_count == 0
    assert [item.reason for item in result.excluded] == [
        "The file was activated after the apply."
    ]
    # The activated file is untouched.
    assert store.get(created.id, owner_id=OWNER).status == "active"


def test_revert_apply_group_excludes_files_edited_after_apply() -> None:
    from datetime import datetime, timedelta, timezone

    from superset_ai_agent.semantic_layer.copilot.service import revert_apply_group
    from superset_ai_agent.semantic_layer.schemas import MdlFileUpdateRequest

    store, created = _mdl_store_with_file()
    store.update(
        created.id,
        MdlFileUpdateRequest(content='{"models": [{"name": "manual"}]}'),
        owner_id=OWNER,
    )
    stale_applied_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = revert_apply_group(
        store,
        [
            _snapshot(
                op="update",
                path="models/a.json",
                file_id=created.id,
                before_content="{}",
                applied_at=stale_applied_at,
            )
        ],
        apply_group_id="group-1",
        project_id="proj-1",
        owner_id=OWNER,
    )
    assert result.reverted_count == 0
    assert [item.reason for item in result.excluded] == [
        "The file was edited after the apply."
    ]


def test_revert_apply_group_recreates_deleted_files() -> None:
    from superset_ai_agent.semantic_layer.copilot.service import revert_apply_group

    store, created = _mdl_store_with_file()
    store.delete(created.id, owner_id=OWNER)
    result = revert_apply_group(
        store,
        [
            _snapshot(
                op="delete",
                path="models/a.json",
                file_id=created.id,
                before_content='{"models": []}',
                before_status="draft",
            )
        ],
        apply_group_id="group-1",
        project_id="proj-1",
        owner_id=OWNER,
    )
    assert result.reverted_count == 1
    restored = [
        file
        for file in store.list("proj-1", owner_id=OWNER)
        if file.path == "models/a.json" and file.status != "deleted"
    ]
    assert len(restored) == 1
    assert restored[0].content == '{"models": []}'
    assert restored[0].status == "draft"


def test_apply_snapshot_store_groups_and_marks_reverted() -> None:
    from superset_ai_agent.semantic_layer.apply_snapshots import (
        ApplySnapshotStore,
        group_snapshots,
    )

    snapshots = ApplySnapshotStore(_sqlalchemy_factory())
    group_id = snapshots.record(
        project_id="proj-1",
        owner_id=OWNER,
        entries=[
            ApplySnapshotEntry(op="create", path="models/a.json", file_id="f1"),
            ApplySnapshotEntry(op="update", path="models/b.json", file_id="f2"),
        ],
        conversation_id="conv-1",
        message_id="msg-applied",
    )
    groups = group_snapshots(
        snapshots.list_for_conversation("conv-1", include_reverted=True)
    )
    assert len(groups) == 1
    assert groups[0].apply_group_id == group_id
    assert groups[0].message_id == "msg-applied"
    assert groups[0].reverted is False
    assert {item.path for item in groups[0].items} == {
        "models/a.json",
        "models/b.json",
    }

    assert snapshots.mark_reverted(group_id) == 2
    regrouped = group_snapshots(
        snapshots.list_for_conversation("conv-1", include_reverted=True)
    )
    assert regrouped[0].reverted is True
    # Non-reverted view is now empty.
    assert snapshots.list_for_conversation("conv-1") == []


# -- Rewrite preview (1a.4) ---------------------------------------------------


def test_rewrite_preview_attributes_applied_items_via_snapshots() -> None:
    factory = _sqlalchemy_factory()
    store = SqlAlchemyConversationStore(factory)
    snapshots = ApplySnapshotStore(factory)
    memory = SqlAlchemyMemory(factory)

    conversation = store.create(_scope(), owner_id=OWNER)
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="model it"),
        owner_id=OWNER,
    )
    store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="Proposal.",
            artifacts=[ConversationArtifact(type="changeset", payload={"items": []})],
        ),
        owner_id=OWNER,
    )
    store.append(
        conversation.id,
        ConversationMessage(role="assistant", content="Applied 1 draft."),
        owner_id=OWNER,
    )
    thread = store.get(conversation.id, owner_id=OWNER)
    anchor = thread.messages[0]
    applied_turn = thread.messages[2]
    memory.store_confirmed(
        question="model it",
        semantic_sql="select 1",
        native_sql="select 1",
        database_id=1,
        source_message_id=anchor.id,
    )
    snapshots.record(
        project_id="proj-1",
        owner_id=OWNER,
        entries=[ApplySnapshotEntry(op="create", path="models/a.json", file_id="f1")],
        conversation_id=conversation.id,
        message_id=applied_turn.id,
    )

    preview = build_rewrite_preview(
        thread, anchor.id, memory=memory, snapshots=snapshots
    )
    assert preview.removed_message_count == 3
    assert preview.memory_write_count == 1
    assert [item.path for item in preview.applied_changeset_items] == ["models/a.json"]
    assert preview.unknown_applies is False
    assert len(preview.apply_group_ids) == 1


def test_rewrite_preview_flags_legacy_applies_without_snapshots() -> None:
    store = _sqlalchemy_store()
    conversation = store.create(_scope(), owner_id=OWNER)
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="model it"),
        owner_id=OWNER,
    )
    store.append(
        conversation.id,
        ConversationMessage(role="assistant", content="Applied 2 drafts."),
        owner_id=OWNER,
    )
    thread = store.get(conversation.id, owner_id=OWNER)

    preview = build_rewrite_preview(thread, thread.messages[0].id)
    assert preview.unknown_applies is True
    assert preview.applied_changeset_items == []


def test_rewrite_preview_ignores_applies_outside_cut_range() -> None:
    factory = _sqlalchemy_factory()
    store = SqlAlchemyConversationStore(factory)
    snapshots = ApplySnapshotStore(factory)
    conversation = store.create(_scope(), owner_id=OWNER)
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="model it"),
        owner_id=OWNER,
    )
    store.append(
        conversation.id,
        ConversationMessage(role="assistant", content="Applied 1 draft."),
        owner_id=OWNER,
    )
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="now add synonyms"),
        owner_id=OWNER,
    )
    thread = store.get(conversation.id, owner_id=OWNER)
    snapshots.record(
        project_id="proj-1",
        owner_id=OWNER,
        entries=[ApplySnapshotEntry(op="create", path="models/a.json", file_id="f1")],
        conversation_id=conversation.id,
        message_id=thread.messages[1].id,
    )

    # Rewriting from the SECOND user turn leaves the earlier apply untouched.
    preview = build_rewrite_preview(thread, thread.messages[2].id, snapshots=snapshots)
    assert preview.removed_message_count == 1
    assert preview.applied_changeset_items == []
    assert preview.unknown_applies is False
