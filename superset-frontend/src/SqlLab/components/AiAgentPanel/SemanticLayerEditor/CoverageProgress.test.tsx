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
import CoverageProgress from './CoverageProgress';

test('degrades to an indeterminate state with no progress payload', () => {
  render(<CoverageProgress />);
  expect(screen.getByText('Analysing coverage…')).toBeInTheDocument();
  // No countable denominator → no determinate bar.
  expect(screen.queryByTestId('coverage-progress-bar')).not.toBeInTheDocument();
});

test('shows the stage detail and a determinate bar when countable', () => {
  render(
    <CoverageProgress
      progress={{
        stage: 'extracting',
        detail: 'orders.pdf',
        current: 2,
        total: 5,
      }}
    />,
  );
  expect(screen.getByText('orders.pdf')).toBeInTheDocument();
  expect(screen.getByTestId('coverage-progress-bar')).toBeInTheDocument();
});

test('judging stage shows no bar (single batched call is not countable)', () => {
  render(
    <CoverageProgress
      progress={{ stage: 'judging', detail: '142 claims vs 38 facts' }}
    />,
  );
  expect(screen.getByText('142 claims vs 38 facts')).toBeInTheDocument();
  expect(screen.queryByTestId('coverage-progress-bar')).not.toBeInTheDocument();
});
