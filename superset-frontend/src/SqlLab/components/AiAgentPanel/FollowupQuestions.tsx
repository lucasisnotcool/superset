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
import { Button, Flex } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';

export interface FollowupQuestionsProps {
  questions?: string[];
  onSelect: (question: string) => void;
  disabled?: boolean;
}

export default function FollowupQuestions({
  questions = [],
  onSelect,
  disabled = false,
}: FollowupQuestionsProps) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <Flex
      aria-label={t('Recommended follow-up questions')}
      gap="small"
      wrap="wrap"
    >
      {questions.slice(0, 3).map(question => (
        <Button
          key={question}
          aria-label={question}
          buttonStyle="tertiary"
          buttonSize="small"
          disabled={disabled}
          onClick={() => onSelect(question)}
          icon={<Icons.QuestionCircleOutlined iconSize="m" />}
        >
          {question}
        </Button>
      ))}
    </Flex>
  );
}
