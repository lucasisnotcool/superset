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
import { createElement } from 'react';
import { render } from 'spec/helpers/testing-library';
import { useProjectEvents } from './useProjectEvents';

function Harness(props: {
  projectId: string | null;
  types: string[];
  onEvent: (type: string) => void;
  enabled?: boolean;
}) {
  useProjectEvents(props.projectId, props.types, props.onEvent, props.enabled);
  return null;
}

class MockEventSource {
  listeners: Record<string, Array<() => void>> = {};

  closed = false;

  static instances: MockEventSource[] = [];

  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: () => void) {
    (this.listeners[type] ??= []).push(handler);
  }

  removeEventListener(type: string, handler: () => void) {
    this.listeners[type] = (this.listeners[type] ?? []).filter(
      h => h !== handler,
    );
  }

  emit(type: string) {
    (this.listeners[type] ?? []).forEach(h => h());
  }

  close() {
    this.closed = true;
  }
}

const originalEventSource = globalThis.EventSource;

beforeEach(() => {
  MockEventSource.instances = [];
  // @ts-ignore - test double
  globalThis.EventSource = MockEventSource;
});

afterEach(() => {
  globalThis.EventSource = originalEventSource;
});

test('subscribes to each event type and invokes the callback', () => {
  const onEvent = jest.fn();
  render(
    createElement(Harness, { projectId: 'p1', types: ['a', 'b'], onEvent }),
  );

  const source = MockEventSource.instances[0];
  source.emit('a');
  source.emit('b');
  expect(onEvent).toHaveBeenCalledWith('a');
  expect(onEvent).toHaveBeenCalledWith('b');
});

test('does not connect when disabled or projectId is missing', () => {
  const onEvent = jest.fn();
  render(
    createElement(Harness, {
      projectId: 'p1',
      types: ['a'],
      onEvent,
      enabled: false,
    }),
  );
  render(createElement(Harness, { projectId: null, types: ['a'], onEvent }));
  expect(MockEventSource.instances).toHaveLength(0);
});

test('closes the connection on unmount', () => {
  const { unmount } = render(
    createElement(Harness, {
      projectId: 'p1',
      types: ['a'],
      onEvent: jest.fn(),
    }),
  );
  const source = MockEventSource.instances[0];
  unmount();
  expect(source.closed).toBe(true);
});
