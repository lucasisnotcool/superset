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
import { useEffect, useRef } from 'react';
import { createProjectSemanticLayerEventSource } from '../api';

/**
 * Subscribe to a project's semantic-layer event stream and invoke `onEvent` when
 * one of `eventTypes` arrives. The backend emits named SSE frames
 * (`event: <type>`), so each type is registered via `addEventListener` (the
 * default `onmessage` never fires for named events).
 *
 * `enabled` gates the connection so a closed dialog / unmounted badge holds none.
 * `onEvent` is stored in a ref so a changing callback identity does not tear down
 * and rebuild the EventSource.
 */
export function useProjectEvents(
  projectId: string | null | undefined,
  eventTypes: string[],
  onEvent: (type: string) => void,
  enabled = true,
): void {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  // Stable across renders so the effect dependency is the *content*, not identity.
  const typesKey = eventTypes.join(',');

  useEffect(() => {
    if (!enabled || !projectId || typeof EventSource === 'undefined') {
      return undefined;
    }
    const source = createProjectSemanticLayerEventSource(projectId);
    const types = typesKey ? typesKey.split(',') : [];
    const handlers = types.map(type => {
      const handler = () => onEventRef.current(type);
      source.addEventListener(type, handler);
      return [type, handler] as const;
    });
    return () => {
      handlers.forEach(([type, handler]) =>
        source.removeEventListener(type, handler),
      );
      source.close();
    };
  }, [projectId, typesKey, enabled]);
}

/** Event types that change a project's coverage/provenance surface. */
export const COVERAGE_EVENT_TYPES = [
  'coverage_completed',
  // Live, non-provenance stage ticks (Feature C) — nudge the badge to re-poll
  // status so the analysing label/stepper advances during a run.
  'coverage_progress',
  'mdl_activated',
  'mdl_deleted',
  'mdl_agent_edit',
  'onboarding_completed',
];
