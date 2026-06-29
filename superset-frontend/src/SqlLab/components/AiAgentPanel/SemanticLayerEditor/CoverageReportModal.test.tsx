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
import { CoverageReport } from '../api';
import CoverageReportModal from './CoverageReportModal';

const report: CoverageReport = {
  document_id: 'd1',
  document_filename: 'glossary.md',
  total: 2,
  covered: 1,
  partial: 0,
  missing: 1,
  score: 0.5,
  overreach: [],
  unsupported: 0,
  warnings: [],
  findings: [
    {
      claim: {
        kind: 'definition',
        subject: 'net_amount',
        statement: 'gross - refunds',
      },
      status: 'covered',
      matched: 'column:orders.net_amount',
    },
    {
      claim: {
        kind: 'synonym',
        subject: 'patty',
        statement: 'a drive unit is a patty',
      },
      status: 'missing',
      suggestion: 'add instruction: patty means drive_unit',
    },
  ],
};

test('renders the score, counts, and per-claim findings', () => {
  render(<CoverageReportModal open report={report} onClose={jest.fn()} />);

  expect(screen.getByText('50% covered')).toBeInTheDocument();
  expect(screen.getByText('a drive unit is a patty')).toBeInTheDocument();
  expect(
    screen.getByText('Fix: add instruction: patty means drive_unit'),
  ).toBeInTheDocument();
  expect(screen.getAllByTestId('coverage-finding')).toHaveLength(2);
});

test('shows the source document tag for directory-level findings', () => {
  const directoryReport = {
    ...report,
    findings: [
      { ...report.findings[0], document_filename: 'orders.md' },
      { ...report.findings[1], document_filename: 'glossary.md' },
    ],
  };
  render(
    <CoverageReportModal open report={directoryReport} onClose={jest.fn()} />,
  );

  const sources = screen.getAllByTestId('coverage-finding-source');
  expect(sources).toHaveLength(2);
  expect(sources[0]).toHaveTextContent('orders.md');
  expect(sources[1]).toHaveTextContent('glossary.md');
});

test('virtualizes a large claims list (renders only the visible window)', () => {
  const manyFindings = Array.from({ length: 200 }, (_, index) => ({
    claim: {
      kind: 'definition' as const,
      subject: `subject_${index}`,
      statement: `statement number ${index}`,
    },
    status: 'covered' as const,
  }));
  render(
    <CoverageReportModal
      open
      report={{ ...report, findings: manyFindings }}
      onClose={jest.fn()}
    />,
  );

  // The list mounts far fewer rows than the 200 findings (react-window only
  // renders the visible window plus a small overscan).
  const rendered = screen.getAllByTestId('coverage-finding');
  expect(rendered.length).toBeGreaterThan(0);
  expect(rendered.length).toBeLessThan(manyFindings.length);
});

test('keeps the score and badges in a pinned summary alongside extra content', () => {
  render(<CoverageReportModal open report={report} onClose={jest.fn()} />);

  const summary = screen.getByTestId('coverage-summary');
  expect(summary).toHaveTextContent('50% covered');
  expect(summary).toHaveTextContent('1 covered');
  expect(summary).toHaveTextContent('1 missing');
});

test('shows a loading state while auditing', () => {
  render(
    <CoverageReportModal open report={null} loading onClose={jest.fn()} />,
  );

  expect(screen.getByTestId('coverage-loading')).toBeInTheDocument();
});

test('shows an error', () => {
  render(
    <CoverageReportModal open report={null} error="boom" onClose={jest.fn()} />,
  );

  expect(screen.getByText('boom')).toBeInTheDocument();
});
