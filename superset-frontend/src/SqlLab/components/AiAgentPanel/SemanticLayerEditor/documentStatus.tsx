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
import { t } from '@apache-superset/core/translation';
import { Tag, Tooltip } from '@superset-ui/core/components';

/** Compact human-readable file size (shared by the detail pane and uploader). */
export const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/**
 * Display metadata for an uploaded document's lifecycle status.
 *
 * Keyed by the raw status string (not the `SemanticDocumentStatus` union in
 * `api.ts`) on purpose: the backend emits `extracting` and `needs_ocr`
 * (document_format_tier1_plan.md) which the shared api type does not yet list.
 * Looking up by string lets this surface those states without editing `api.ts`
 * (owned by a parallel workstream). See the api.ts union TODO in that plan's
 * as-built notes.
 */
export interface DocumentStatusMeta {
  /** Human-friendly, translated label. */
  label: string;
  /** antd Tag color; undefined → default (neutral) tag. */
  color?: string;
  /** Optional explanation shown on hover. */
  tooltip?: string;
  /**
   * Whether the status warrants a badge in the compact tree view. Normal,
   * expected states (uploaded/extracted and legacy ok states) stay quiet there
   * to avoid clutter; only states needing the user's attention surface.
   */
  attention: boolean;
}

export const getDocumentStatusMeta = (status: string): DocumentStatusMeta => {
  switch (status) {
    case 'extracting':
      return {
        label: t('Extracting…'),
        color: 'processing',
        tooltip: t(
          'Text is being extracted from this document in the background.',
        ),
        attention: true,
      };
    case 'needs_ocr':
      return {
        label: t('Needs OCR'),
        color: 'warning',
        tooltip: t(
          'This document has no extractable text layer (it is likely scanned ' +
            'or image-only). OCR is required to read its contents.',
        ),
        attention: true,
      };
    case 'error':
      return {
        label: t('Error'),
        color: 'error',
        tooltip: t('Text could not be extracted from this document.'),
        attention: true,
      };
    case 'extracted':
      return { label: t('Extracted'), color: 'success', attention: false };
    case 'uploaded':
      return { label: t('Uploaded'), attention: false };
    // Legacy statuses retained for read-compat with older rows.
    case 'indexed':
      return { label: t('Indexed'), color: 'success', attention: false };
    case 'approved':
      return { label: t('Approved'), color: 'success', attention: false };
    case 'needs_review':
      return { label: t('Needs review'), color: 'warning', attention: true };
    default:
      return { label: status, attention: false };
  }
};

export interface DocumentStatusTagProps {
  status: string;
  /** When the status is `error`, the backend message is shown on hover. */
  error?: string | null;
}

/**
 * A status tag with an explanatory tooltip, shared by the document detail pane
 * and the workspace tree so the two views stay consistent.
 */
export const DocumentStatusTag = ({
  status,
  error,
}: DocumentStatusTagProps) => {
  const meta = getDocumentStatusMeta(status);
  const tag = <Tag color={meta.color}>{meta.label}</Tag>;
  const tooltip = status === 'error' && error ? error : meta.tooltip;
  return tooltip ? <Tooltip title={tooltip}>{tag}</Tooltip> : tag;
};
