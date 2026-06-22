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
import { render, screen } from 'spec/helpers/testing-library';
import MarkdownCodeBlock from './MarkdownCodeBlock';

jest.mock('@superset-ui/core/components/CodeSyntaxHighlighter', () =>
  // eslint-disable-next-line react/display-name
  ({ children, language }: { children: string; language: string }) => (
    <pre data-test="syntax-highlighter" data-language={language}>
      {children}
    </pre>
  ),
);

test('renders inline code as a plain code element', () => {
  render(
    <MarkdownCodeBlock inline className="language-js">
      x = 1
    </MarkdownCodeBlock>,
  );
  const code = screen.getByText('x = 1');
  expect(code.tagName).toBe('CODE');
  expect(screen.queryByTestId('syntax-highlighter')).not.toBeInTheDocument();
});

test('highlights a fenced SQL block', () => {
  render(
    <MarkdownCodeBlock className="language-sql">
      SELECT 1 FROM t
    </MarkdownCodeBlock>,
  );
  const block = screen.getByTestId('syntax-highlighter');
  expect(block).toHaveAttribute('data-language', 'sql');
  expect(block).toHaveTextContent('SELECT 1 FROM t');
});

test('treats an unlabeled fenced block as SQL', () => {
  render(<MarkdownCodeBlock>SELECT 2</MarkdownCodeBlock>);
  expect(screen.getByTestId('syntax-highlighter')).toHaveAttribute(
    'data-language',
    'sql',
  );
});

test('falls back to a plain block for unsupported languages', () => {
  render(
    <MarkdownCodeBlock className="language-python">print(1)</MarkdownCodeBlock>,
  );
  expect(screen.queryByTestId('syntax-highlighter')).not.toBeInTheDocument();
  expect(screen.getByText('print(1)').tagName).toBe('CODE');
});
