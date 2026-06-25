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
