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
import AuditInfoPanel from './AuditInfoPanel';
import type { AuditInfo, WrenContextArtifact } from './api';

test('returns nothing without audit', () => {
  const { container } = render(<AuditInfoPanel audit={null} />);
  expect(container).toBeEmptyDOMElement();
});

test('surfaces engine provenance with friendly labels', () => {
  const audit: AuditInfo = {
    engine: 'wren_core',
    semantic_sql: 'SELECT name FROM customers',
    native_sql: 'SELECT name FROM sales.customers',
  };
  render(<AuditInfoPanel audit={audit} />);
  // Engine badge is shown at a glance.
  expect(screen.getByText('Engine: wren_core')).toBeInTheDocument();
  // Semantic vs native SQL are labeled (not raw snake_case keys).
  expect(screen.getByText('Semantic SQL')).toBeInTheDocument();
  expect(screen.getByText('Native SQL')).toBeInTheDocument();
  expect(
    screen.getByText('SELECT name FROM sales.customers'),
  ).toBeInTheDocument();
});

const baseWrenContext: WrenContextArtifact = {
  enabled: true,
  available: true,
  matched_models: [],
  example_ids: [],
  document_ids: [],
  context_items: [],
  warnings: [],
};

test('shows the retrieval mode from the wren context', () => {
  const audit: AuditInfo = { engine: 'wren_core' };
  render(
    <AuditInfoPanel
      audit={audit}
      wrenContext={{ ...baseWrenContext, retrieval_mode: 'embedding' }}
    />,
  );
  expect(screen.getByText('Retrieval: embedding')).toBeInTheDocument();
});

test('shows retrieved chunk count alongside the retrieval mode', () => {
  render(
    <AuditInfoPanel
      audit={{ engine: 'wren_core' }}
      wrenContext={{
        ...baseWrenContext,
        retrieval_mode: 'embedding',
        retrieved_item_count: 5,
      }}
    />,
  );
  expect(
    screen.getByText('Retrieval: embedding (5 chunks)'),
  ).toBeInTheDocument();
});

test('badges reused learned examples when recall fired', () => {
  render(
    <AuditInfoPanel
      audit={{ engine: 'wren_core' }}
      wrenContext={{ ...baseWrenContext, recalled_example_count: 2 }}
    />,
  );
  expect(screen.getByText('Reused 2 learned example(s)')).toBeInTheDocument();
});

test('omits the memory badge when nothing was recalled', () => {
  render(
    <AuditInfoPanel
      audit={{ engine: 'wren_core' }}
      wrenContext={{ ...baseWrenContext, recalled_example_count: 0 }}
    />,
  );
  expect(screen.queryByText(/learned example/)).not.toBeInTheDocument();
});

test('badges surfaced views when a view grounded the answer', () => {
  render(
    <AuditInfoPanel
      audit={{ engine: 'wren_core' }}
      wrenContext={{
        ...baseWrenContext,
        matched_views: ['warm_line_output', 'golden_yield'],
      }}
    />,
  );
  expect(screen.getByText('Surfaced 2 view(s)')).toBeInTheDocument();
});

test('omits the view badge when no view was surfaced', () => {
  render(
    <AuditInfoPanel
      audit={{ engine: 'wren_core' }}
      wrenContext={{ ...baseWrenContext, matched_views: [] }}
    />,
  );
  expect(screen.queryByText(/view\(s\)/)).not.toBeInTheDocument();
});
