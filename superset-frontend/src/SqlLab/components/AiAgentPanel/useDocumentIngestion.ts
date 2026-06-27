/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { useCallback, useState } from 'react';
import { useDispatch } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import {
  addDangerToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import { SemanticDocument, uploadProjectSourceDocument } from './api';

export interface DocumentIngestionResult {
  /** The persisted document — either freshly created or the reused duplicate. */
  document: SemanticDocument;
  /** True when the upload was deduplicated to a pre-existing identical document. */
  deduplicated: boolean;
}

export interface UseDocumentIngestion {
  /**
   * Upload + dedup + (server-side) extract + vectorize each file through the one
   * persistent pipeline, returning the persisted documents. This is the single
   * ingestion path shared by the Copilot "Attach" and the "Upload document"
   * button — the only difference between those two ingress points lives in their
   * own callers (Attach additionally inlines the document into the chat turn).
   *
   * Per-file errors are toasted and dropped from the result; the returned array
   * holds only successful documents, in input order.
   */
  ingest: (files: File[]) => Promise<DocumentIngestionResult[]>;
  /** True while any upload is in flight. */
  isIngesting: boolean;
}

/**
 * Shared document-ingestion hook. Dispatches its own success/reuse/error toasts so
 * both ingress points notify the user consistently; callers handle only the
 * resulting documents (refresh the tree, and — for Attach — ground the turn).
 */
export default function useDocumentIngestion(
  projectId: string | null,
): UseDocumentIngestion {
  const dispatch = useDispatch();
  const [isIngesting, setIsIngesting] = useState(false);

  const ingest = useCallback(
    async (files: File[]): Promise<DocumentIngestionResult[]> => {
      if (!projectId || files.length === 0) {
        return [];
      }
      setIsIngesting(true);
      try {
        const settled = await Promise.all(
          files.map(async file => {
            try {
              const document = await uploadProjectSourceDocument(
                projectId,
                file,
              );
              const deduplicated = document.deduplicated === true;
              if (deduplicated) {
                dispatch(
                  addSuccessToast(
                    t(
                      '“%s” is already in this project — reusing it.',
                      document.filename,
                    ),
                  ),
                );
              } else {
                dispatch(
                  addSuccessToast(t('Uploaded “%s”.', document.filename)),
                );
              }
              return { document, deduplicated };
            } catch (caught) {
              dispatch(
                addDangerToast(
                  t(
                    'Could not upload “%s”: %s',
                    file.name,
                    caught instanceof Error ? caught.message : String(caught),
                  ),
                ),
              );
              return null;
            }
          }),
        );
        return settled.filter(
          (result): result is DocumentIngestionResult => result !== null,
        );
      } finally {
        setIsIngesting(false);
      }
    },
    [dispatch, projectId],
  );

  return { ingest, isIngesting };
}
