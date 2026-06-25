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
import { css, useTheme } from '@apache-superset/core/theme';
import {
  Empty,
  Flex,
  Modal,
  Tabs,
  Typography,
} from '@superset-ui/core/components';
import { CopilotInspector } from '../api';

export interface CopilotInspectorDialogProps {
  open: boolean;
  inspector: CopilotInspector | null;
  onClose: () => void;
}

const Pre = ({ children }: { children: string }) => {
  const theme = useTheme();
  return (
    <pre
      css={css`
        white-space: pre-wrap;
        word-break: break-word;
        background: ${theme.colorBgLayout};
        border: 1px solid ${theme.colorBorderSecondary};
        border-radius: ${theme.borderRadius}px;
        padding: ${theme.sizeUnit * 2}px;
        margin: 0;
        font-size: ${theme.fontSizeSM}px;
      `}
    >
      {children}
    </pre>
  );
};

/**
 * The agent inspector, shown as a dialog (mirroring the AI "How this answer was
 * produced" explain dialog) — but surfacing the agent's effective parameters
 * (prompt, instructions, skills, tools) instead of a query's reasoning steps.
 */
const CopilotInspectorDialog = ({
  open,
  inspector,
  onClose,
}: CopilotInspectorDialogProps) => {
  const theme = useTheme();

  const body = inspector ? (
    <Tabs
      data-test="copilot-inspector-tabs"
      items={[
        {
          key: 'prompt',
          label: t('Prompt'),
          children: <Pre>{inspector.system_prompt}</Pre>,
        },
        {
          key: 'instructions',
          label: t('Instructions'),
          children: inspector.instructions.length ? (
            <Flex vertical gap={theme.sizeUnit}>
              {inspector.instructions.map(instruction => (
                <Typography.Text key={instruction.id}>
                  • {instruction.instruction}
                  {instruction.is_global ? ` (${t('global')})` : ''}
                </Typography.Text>
              ))}
            </Flex>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t(
                'No schema instructions yet. Add them in the Instructions tab.',
              )}
            />
          ),
        },
        {
          key: 'skills',
          label: t('Skills'),
          children: (
            <Flex vertical gap={theme.sizeUnit * 2}>
              {inspector.skills.map(skill => (
                <Flex vertical key={skill.name} gap={theme.sizeUnit}>
                  <Typography.Text strong>{skill.name}</Typography.Text>
                  <Pre>{skill.text}</Pre>
                </Flex>
              ))}
            </Flex>
          ),
        },
        {
          key: 'tools',
          label: t('Tools'),
          children: (
            <Flex vertical gap={theme.sizeUnit}>
              {inspector.tools.map(tool => (
                <Flex vertical key={tool.name}>
                  <Typography.Text code>{tool.name}</Typography.Text>
                  <Typography.Text type="secondary">
                    {tool.description}
                  </Typography.Text>
                </Flex>
              ))}
            </Flex>
          ),
        },
      ]}
    />
  ) : (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={t('Loading agent context…')}
    />
  );

  return (
    <Modal
      show={open}
      onHide={onClose}
      title={t('How the agent is configured')}
      hideFooter
      destroyOnHidden
      responsive
      data-test="copilot-inspector-dialog"
    >
      {body}
    </Modal>
  );
};

export default CopilotInspectorDialog;
