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
import { useEffect, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import {
  Button,
  ConfirmModal,
  Empty,
  Flex,
  List,
  SafeMarkdown,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  deleteSemanticDocument,
  DocumentChunk,
  downloadDocumentUrl,
  listDocumentChunks,
  reindexSemanticDocument,
  SemanticDocument,
  summarizeSemanticDocument,
} from '../api';
import { DocumentStatusTag, formatBytes } from './documentStatus';

export interface DocumentDetailPaneProps {
  document: SemanticDocument;
  canWrite: boolean;
  onDeleted: () => void;
  onChanged: () => void;
}

const Scroll = styled.div`
  ${({ theme }) => css`
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: ${theme.sizeUnit * 2}px;
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgContainer};
  `}
`;

const Pre = styled.pre`
  ${({ theme }) => css`
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: ${theme.fontFamilyCode};
    font-size: ${theme.fontSizeSM}px;
  `}
`;

const isMarkdown = (contentType: string) =>
  contentType.includes('markdown') || contentType.includes('text/plain');

const DocumentText = ({ document }: { document: SemanticDocument }) => {
  const text = document.extracted_text ?? document.extracted_text_preview ?? '';
  if (!text) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('No extracted text.')}
      />
    );
  }
  return (
    <Scroll>
      {isMarkdown(document.content_type) ? (
        <SafeMarkdown source={text} />
      ) : (
        <Pre>{text}</Pre>
      )}
    </Scroll>
  );
};

const DocumentChunks = ({ documentId }: { documentId: string }) => {
  const [chunks, setChunks] = useState<DocumentChunk[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let mounted = true;
    setChunks(null);
    setUnavailable(false);
    listDocumentChunks(documentId)
      .then(result => mounted && setChunks(result))
      .catch(() => mounted && setUnavailable(true));
    return () => {
      mounted = false;
    };
  }, [documentId]);

  if (unavailable) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('Chunking is disabled for this deployment.')}
      />
    );
  }
  if (!chunks) {
    return <Typography.Text type="secondary">{t('Loading…')}</Typography.Text>;
  }
  if (chunks.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('No chunks for this document.')}
      />
    );
  }
  return (
    <Scroll>
      <List
        size="small"
        dataSource={chunks}
        renderItem={(chunk: DocumentChunk) => (
          <List.Item>
            <Flex vertical gap={2} style={{ width: '100%' }}>
              <Flex align="center" gap={4}>
                <Tag>{`#${chunk.chunk_index}`}</Tag>
                <Typography.Text type="secondary">
                  {`${chunk.text.length} ${t('chars')}`}
                </Typography.Text>
                {chunk.embedded ? (
                  <Tag color="success">{t('embedded')}</Tag>
                ) : null}
              </Flex>
              <Typography.Text>{chunk.text}</Typography.Text>
            </Flex>
          </List.Item>
        )}
      />
    </Scroll>
  );
};

const DocumentDetailPane = ({
  document,
  canWrite,
  onDeleted,
  onChanged,
}: DocumentDetailPaneProps) => {
  const [busy, setBusy] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const runAction = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Flex vertical gap="small" style={{ height: '100%', minHeight: 0 }}>
      <Flex align="center" gap={4} wrap="wrap">
        <Icons.FileOutlined />
        <Typography.Text strong>{document.filename}</Typography.Text>
        <Typography.Text type="secondary">
          {`${document.content_type} · ${formatBytes(document.size_bytes)}`}
        </Typography.Text>
        <DocumentStatusTag status={document.status} error={document.error} />
      </Flex>
      {document.status === 'needs_ocr' ? (
        <Typography.Text type="warning">
          {t(
            'No text could be read from this file — it looks scanned or ' +
              'image-only. The original is stored and can be re-processed once ' +
              'OCR is available.',
          )}
        </Typography.Text>
      ) : null}
      <Tabs
        defaultActiveKey="text"
        css={css`
          flex: 1;
          min-height: 0;
          .ant-tabs-content,
          .ant-tabs-tabpane {
            height: 100%;
          }
          .ant-tabs-content-holder {
            display: flex;
          }
        `}
        items={[
          {
            key: 'text',
            label: t('Text'),
            children: <DocumentText document={document} />,
          },
          {
            key: 'chunks',
            label: t('Chunks'),
            children: <DocumentChunks documentId={document.id} />,
          },
          {
            key: 'summary',
            label: t('Summary'),
            children: (
              <Scroll>
                {document.summary ? (
                  <Typography.Paragraph>
                    {document.summary}
                  </Typography.Paragraph>
                ) : (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={t('No summary yet.')}
                  />
                )}
              </Scroll>
            ),
          },
        ]}
      />
      {/* Action bar at the bottom, mirroring the MDL file editor layout
          (item 3): primary/secondary actions on the left, destructive on the
          right. Re-index and Summarize carry hover help text (item 4); Download
          and Delete are self-explanatory and intentionally have none. */}
      <Flex justify="space-between" gap="small" wrap="wrap">
        <Flex gap="small" wrap="wrap">
          <Button
            href={downloadDocumentUrl(document.id)}
            icon={<Icons.DownloadOutlined iconSize="m" />}
          >
            {t('Download')}
          </Button>
          <Tooltip
            title={t(
              'Rebuild this document’s search chunks (re-chunk and re-embed) ' +
                'so retrieval reflects the latest content.',
            )}
          >
            <Button
              buttonStyle="tertiary"
              loading={busy}
              disabled={!canWrite || busy}
              onClick={() =>
                runAction(() => reindexSemanticDocument(document.id))
              }
              icon={<Icons.ReloadOutlined iconSize="m" />}
            >
              {t('Re-index')}
            </Button>
          </Tooltip>
          <Tooltip
            title={t(
              'Generate a fresh AI summary of this document from its ' +
                'extracted text.',
            )}
          >
            <Button
              buttonStyle="tertiary"
              loading={busy}
              disabled={!canWrite || busy}
              onClick={() =>
                runAction(() => summarizeSemanticDocument(document.id))
              }
              icon={<Icons.FileTextOutlined iconSize="m" />}
            >
              {t('Summarize')}
            </Button>
          </Tooltip>
        </Flex>
        <Button
          buttonStyle="danger"
          disabled={!canWrite || busy}
          onClick={() => setShowDeleteConfirm(true)}
          icon={<Icons.DeleteOutlined iconSize="m" />}
        >
          {t('Delete')}
        </Button>
      </Flex>
      <ConfirmModal
        show={showDeleteConfirm}
        onHide={() => setShowDeleteConfirm(false)}
        onConfirm={async () => {
          await deleteSemanticDocument(document.id);
          onDeleted();
        }}
        confirmText={t('Delete')}
        confirmButtonStyle="danger"
        title={t('Delete document?')}
        body={t(
          'This permanently removes the document, its chunks, and its file.',
        )}
      />
    </Flex>
  );
};

export default DocumentDetailPane;
