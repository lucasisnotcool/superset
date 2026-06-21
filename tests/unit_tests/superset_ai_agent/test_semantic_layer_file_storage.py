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

from __future__ import annotations

from io import BytesIO

import pytest

from superset_ai_agent.semantic_layer.file_storage import (
    LocalDocumentStorage,
    S3DocumentStorage,
)


def test_local_document_storage_round_trips_bytes(tmp_path) -> None:
    storage = LocalDocumentStorage(str(tmp_path))

    uri = storage.write(
        document_id="doc-1",
        filename="../Gross Moves Notes.md",
        content=b"stage means opportunity stage",
    )

    assert storage.read(uri) == b"stage means opportunity stage"
    assert "Gross_Moves_Notes.md" in uri
    storage.delete(uri)
    assert not uri.endswith("../Gross Moves Notes.md")


def test_s3_document_storage_round_trips_bytes() -> None:
    client = FakeS3Client()
    storage = S3DocumentStorage(
        bucket="agent-docs",
        prefix="semantic/docs",
        client=client,
    )

    uri = storage.write(
        document_id="doc/../1",
        filename="../Gross Moves Notes.md",
        content=b"stage means opportunity stage",
    )

    assert uri == (
        "s3://agent-docs/semantic/docs/doc_.._1/Gross_Moves_Notes.md"
    )
    assert storage.read(uri) == b"stage means opportunity stage"
    storage.delete(uri)
    assert client.objects == {}


def test_s3_document_storage_requires_bucket() -> None:
    with pytest.raises(ValueError, match="AI_AGENT_DOCUMENT_S3_BUCKET"):
        S3DocumentStorage(bucket="", client=FakeS3Client())


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)
