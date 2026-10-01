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
import parseServerTiming from './parseServerTiming';

test('returns undefined for missing or empty header', () => {
  expect(parseServerTiming(null)).toBeUndefined();
  expect(parseServerTiming(undefined)).toBeUndefined();
  expect(parseServerTiming('')).toBeUndefined();
});

test('parses durations keyed by metric name', () => {
  expect(
    parseServerTiming('total;dur=80.0, db;dur=53.0, cache;dur=2.0'),
  ).toEqual({
    total: 80,
    db: 53,
    cache: 2,
  });
});

test('ignores the description token and extra whitespace', () => {
  expect(
    parseServerTiming('total;dur=80.0;desc="Total server time",db;dur=53'),
  ).toEqual({ total: 80, db: 53 });
});

test('skips metrics without a numeric dur', () => {
  expect(parseServerTiming('miss, db;dur=10, bad;dur=abc')).toEqual({ db: 10 });
});

test('returns undefined when no metric yields a duration', () => {
  expect(parseServerTiming('miss, cachehit')).toBeUndefined();
});
