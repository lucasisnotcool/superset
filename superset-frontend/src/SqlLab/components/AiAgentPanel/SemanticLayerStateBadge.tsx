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
import { css, styled } from '@apache-superset/core/theme';
import type { SemanticLayerState } from './api';

const Badge = styled.span<{ status?: SemanticLayerState['indexing_status'] }>`
  ${({ theme, status }) => css`
    display: inline-flex;
    align-items: center;
    max-width: 100%;
    height: 24px;
    padding: 0 ${theme.sizeUnit * 2}px;
    border: 1px solid
      ${status === 'error' ? theme.colorErrorBorder : theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
    color: ${status === 'error' ? theme.colorError : theme.colorTextSecondary};
    background: ${theme.colorBgContainer};
    font-size: ${theme.fontSizeSM}px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  `}
`;

export interface SemanticLayerStateBadgeProps {
  state?: SemanticLayerState | null;
}

export default function SemanticLayerStateBadge({
  state,
}: SemanticLayerStateBadgeProps) {
  if (!state) {
    return <Badge>{t('Semantic layer')}</Badge>;
  }
  return (
    <Badge status={state.indexing_status}>
      {state.semantic_layer_version
        ? t('Semantic layer %s', state.semantic_layer_version)
        : t('%s document(s)', state.document_count)}
    </Badge>
  );
}
