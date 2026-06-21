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
import { ChangeEvent, useCallback, useEffect, useRef, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { Button, Flex, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  ConversationScope,
  getSemanticLayerState,
  listSemanticDocuments,
  rebuildSemanticLayerIndex,
  reviewSemanticDocument,
  SemanticDocument,
  SemanticLayerState,
  uploadSemanticDocument,
} from './api';
import SemanticLayerStateBadge from './SemanticLayerStateBadge';

const Overlay = styled.div`
  ${({ theme }) => css`
    position: absolute;
    inset: 0;
    z-index: 2;
    display: flex;
    justify-content: flex-end;
    background: ${theme.colorBgMask};
  `}
`;

const Drawer = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    width: min(460px, 100%);
    height: 100%;
    background: ${theme.colorBgBase};
    border-left: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const DrawerHeader = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const DrawerBody = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex: 1;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 3}px;
    overflow: auto;
  `}
`;

const DocumentItem = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    padding: ${theme.sizeUnit * 2}px 0;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const HiddenFileInput = styled.input`
  display: none;
`;

export interface SemanticLayerDrawerProps {
  open: boolean;
  scope: ConversationScope | null;
  onClose: () => void;
  onStateChange?: (state: SemanticLayerState | null) => void;
}

export default function SemanticLayerDrawer({
  open,
  scope,
  onClose,
  onStateChange,
}: SemanticLayerDrawerProps) {
  const [documents, setDocuments] = useState<SemanticDocument[]>([]);
  const [state, setState] = useState<SemanticLayerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    if (!scope) {
      return;
    }
    const [nextState, nextDocuments] = await Promise.all([
      getSemanticLayerState(scope),
      listSemanticDocuments(scope),
    ]);
    setState(nextState);
    setDocuments(nextDocuments);
    onStateChange?.(nextState);
  }, [onStateChange, scope]);

  useEffect(() => {
    if (open && scope) {
      refresh().catch(ex => {
        setError(
          ex instanceof Error ? ex.message : t('Unable to load documents'),
        );
      });
    }
  }, [open, refresh, scope]);

  if (!open) {
    return null;
  }

  const onUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !scope) {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      await uploadSemanticDocument(scope, file);
      await refresh();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : t('Document upload failed'));
    } finally {
      event.target.value = '';
      setIsLoading(false);
    }
  };

  const onApproveAll = async (document: SemanticDocument) => {
    setIsLoading(true);
    setError(null);
    try {
      await reviewSemanticDocument(document.id, {
        approved_update_ids: document.proposed_updates
          .filter(update => !update.reviewed)
          .map(update => update.id),
        rejected_update_ids: [],
        edited_updates: [],
      });
      await refresh();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : t('Review failed'));
    } finally {
      setIsLoading(false);
    }
  };

  const onRebuild = async () => {
    if (!scope) {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      await rebuildSemanticLayerIndex(scope);
      await refresh();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : t('Index rebuild failed'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Overlay>
      <Drawer role="dialog" aria-label={t('Semantic layer')}>
        <DrawerHeader>
          <Flex vertical gap={0}>
            <Typography.Title level={5} style={{ margin: 0 }}>
              {t('Semantic layer')}
            </Typography.Title>
            <SemanticLayerStateBadge state={state} />
          </Flex>
          <Button
            aria-label={t('Close semantic layer')}
            buttonStyle="tertiary"
            onClick={onClose}
            icon={<Icons.CloseOutlined iconSize="m" />}
          />
        </DrawerHeader>
        <DrawerBody>
          {error && <Alert type="warning" message={error} />}
          {!scope && (
            <Alert type="warning" message={t('Select a database first.')} />
          )}
          <Flex gap="small" wrap="wrap">
            <Button
              buttonStyle="primary"
              disabled={!scope || isLoading}
              onClick={() => fileInputRef.current?.click()}
              icon={<Icons.UploadOutlined iconSize="m" />}
            >
              {t('Upload')}
            </Button>
            <HiddenFileInput
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.csv,.json,text/plain,text/markdown,text/csv,application/json"
              onChange={onUpload}
            />
            <Button
              buttonStyle="tertiary"
              disabled={!scope || isLoading}
              onClick={onRebuild}
              icon={<Icons.SyncOutlined iconSize="m" />}
            >
              {t('Rebuild')}
            </Button>
          </Flex>
          {documents.map(document => (
            <DocumentItem key={document.id}>
              <Typography.Text strong>{document.filename}</Typography.Text>
              <Typography.Text type="secondary">
                {document.status} · {document.proposed_updates.length}{' '}
                {t('update(s)')}
              </Typography.Text>
              {document.summary && (
                <Typography.Paragraph>{document.summary}</Typography.Paragraph>
              )}
              <Flex gap="small" wrap="wrap">
                <Button
                  buttonStyle="tertiary"
                  buttonSize="small"
                  disabled={
                    isLoading ||
                    document.proposed_updates.every(update => update.reviewed)
                  }
                  onClick={() => onApproveAll(document)}
                  icon={<Icons.CheckCircleOutlined iconSize="m" />}
                >
                  {t('Approve')}
                </Button>
              </Flex>
            </DocumentItem>
          ))}
        </DrawerBody>
      </Drawer>
    </Overlay>
  );
}
