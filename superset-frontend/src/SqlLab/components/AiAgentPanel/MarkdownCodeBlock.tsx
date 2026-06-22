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
import type { ReactNode } from 'react';
import CodeSyntaxHighlighter, {
  type SupportedLanguage,
} from '@superset-ui/core/components/CodeSyntaxHighlighter';

interface MarkdownCodeBlockProps {
  inline?: boolean;
  className?: string;
  children?: ReactNode;
}

const SUPPORTED_LANGUAGES: SupportedLanguage[] = [
  'sql',
  'json',
  'markdown',
  'htmlbars',
];

const resolveLanguage = (className?: string): SupportedLanguage | null => {
  const match = /language-(\w+)/.exec(className || '');
  const language = match?.[1]?.toLowerCase();
  if (!language) {
    // Unlabeled fenced blocks in agent answers are overwhelmingly SQL.
    return 'sql';
  }
  return SUPPORTED_LANGUAGES.includes(language as SupportedLanguage)
    ? (language as SupportedLanguage)
    : null;
};

/**
 * react-markdown `code` renderer for agent answers: inline code stays a plain
 * `<code>`, while fenced blocks get syntax highlighting and a copy button
 * (both provided by CodeSyntaxHighlighter). Unsupported languages fall back to
 * a plain preformatted block.
 */
const MarkdownCodeBlock = ({
  inline,
  className,
  children,
}: MarkdownCodeBlockProps) => {
  if (inline) {
    return <code className={className}>{children}</code>;
  }
  const code = String(children ?? '').replace(/\n$/, '');
  const language = resolveLanguage(className);
  if (!language) {
    return (
      <pre>
        <code className={className}>{code}</code>
      </pre>
    );
  }
  return (
    <CodeSyntaxHighlighter language={language} showCopyButton>
      {code}
    </CodeSyntaxHighlighter>
  );
};

export default MarkdownCodeBlock;
